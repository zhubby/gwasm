# gwasm 部署与运维指南

本文说明如何在本机或可信局域网中部署 gwasm，并覆盖首次启动、配置、
持久化、升级、回滚、备份、安全边界和故障排查。项目的实现原理见
[internals.md](internals.md)，面向普通使用者的最短启动路径见
[README.md](../README.md)。

## 1. 部署前先了解边界

gwasm 由一个 Python HTTP 服务和浏览器中的 WebAssembly 客户端组成：

```text
浏览器
  |-- HTTP/Range ----------> gwasm 静态文件与分块缓存
  |-- WebSocket -----------> gwasm TCP relay ----------> ArenaNet 游戏服务
  `-- 同源 Web API --------> gwasm HTTPS proxy --------> Guild Wars Web 服务
```

容器只运行文件服务、下载器和网络中继，画面仍由访问页面的宿主浏览器和
GPU 渲染。部署容器不会在容器内启动浏览器，也不会解决无头环境的 GPU
兼容问题。

当前项目还有两个产品限制：

- **不能登录游戏。** `login` 和 `nativeAccount` 尚未接入，页面中也没有可用的
  凭据输入流程。
- **真实 GPU 上的渲染仍待验证。** 服务和客户端进入 ready 并不等于一定能
  正常显示游戏画面。

部署目标是一个用户或一个小型可信局域网。服务没有应用层登录、租户隔离、
限流或公网防护，**不要直接暴露到互联网，也不要把它当作公共多用户服务**。

## 2. 部署方式选择

| 场景 | 推荐方式 | 说明 |
|---|---|---|
| 当前电脑上的单用户试用 | release zip + Python | 最简单，不需要 Docker |
| 家庭网络或实验室内共享 | Docker Compose | 统一进程管理并持久化 `/data` |
| 修改代码或验证未发布版本 | 从源码构建镜像 | 使用当前 checkout，不依赖 GHCR 最新镜像 |
| 公网、多用户或无人值守生产服务 | 不支持 | 需要额外的认证、隔离、TLS 和运维设计 |

GitHub Release 中的可运行 zip 只包含启动所需文件，不包含 `Dockerfile`、
`compose.yaml` 和 `docs/`。容器部署应克隆仓库或下载 GitHub 的源码归档，
不能使用 release zip 作为 Compose 目录。

## 3. 资源与网络要求

### 3.1 主机要求

- Docker 部署：Docker Engine 或 Docker Desktop/OrbStack，以及近期的 Docker
  Compose v2（使用 `docker compose` 命令，并能识别 `pull_policy`）。
- 本机部署：Python 3.8 或更高版本，只使用标准库，不需要 `pip install`。
- 浏览器：近期版本的 Chrome、Edge 或 Firefox；Chrome 是主要测试目标。
  Safari/iOS 应使用 Asyncify 构建，见[构建类型](#62-构建类型)。
- 磁盘：按需流式运行至少预留约 1 GB；使用 `--image` 下载完整游戏镜像时，
  建议至少预留 5 GB。
- 内存和 CPU 没有硬性配额，但服务使用线程处理并发 Range 请求和长连接，
  不应设置过低的线程/PID 限制。

仓库当前的发布配置以 `linux/amd64` 和 `linux/arm64` 为目标；旧镜像可能只有
`linux/amd64`。在 ARM 主机部署前可检查实际镜像清单：

```bash
docker buildx imagetools inspect ghcr.io/zhubby/gwasm:latest
```

如果输出中没有主机架构，Docker Desktop 或 OrbStack 可能使用模拟运行；也可以
改用较新的多架构版本，或按本文后面的步骤从源码构建原生镜像。

### 3.2 入站与出站网络

默认 Compose 将宿主机 TCP `8080` 映射到容器的 TCP `8080`。防火墙只应允许
需要访问 gwasm 的可信局域网网段连接该端口。默认入口是明文 HTTP，局域网中的
页面和 relay 流量不会被 TLS 加密；不可信网络应先建立受控的私网或私有 HTTPS
入口，而不是直接开放端口。

容器需要以下出站访问：

- HTTPS 到 `patching.1.arenanetworks.com`，用于下载客户端和游戏数据；
- HTTPS 到 `webgate.ncplatform.net`、`account.arena.net`、
  `help.guildwars.com`、`store.guildwars.com` 和 `www.guildwars.com`；
- TCP `6112`、`80`、`443` 到允许的公网游戏地址；
- 正常 DNS；系统解析器无法处理特殊记录时，程序会尝试 UDP 53 的
  `1.1.1.1` 和 `8.8.8.8`。

企业防火墙、透明代理或 DNS 策略拦截这些连接时，首页可能仍然健康，但客户端
下载、DNS 或游戏连接会失败。

## 4. 使用 Docker Compose 部署

### 4.1 获取配置并设置访问地址

```bash
git clone https://github.com/zhubby/gwasm.git
cd gwasm
cp .env.example .env
```

编辑 `.env`，把 `GWASM_ORIGIN` 设为浏览器实际打开的地址：

```dotenv
GWASM_ORIGIN=http://192.168.1.20:8080
```

该值是 WebSocket relay 和 Web API proxy 的精确 Origin 白名单，必须满足：

- 使用 `http://` 或 `https://`；
- 使用浏览器实际访问的 IP 或主机名；
- 显式写端口；
- 不包含路径、查询参数、通配符或末尾 `/`。

例如，浏览器访问 `http://gwasm.lan:9000` 时，Origin 必须是
`http://gwasm.lan:9000`，不能写服务器的另一个 IP，也不能只写
`http://gwasm.lan`。

确实需要同时通过 IP 和主机名访问时，应在 `command` 中重复添加一组
`--origin` 和对应值；每个入口仍需精确列出。Origin 列表不是用户认证，入口越多，
可调用 relay 的页面范围越大。

默认端口映射为所有宿主接口上的 `8080`：

```yaml
ports:
  - "0.0.0.0:8080:8080"
```

如果主机有固定的局域网地址，建议把左侧地址收紧，例如：

```yaml
ports:
  - "192.168.1.20:8080:8080"
```

如果需要改为宿主机端口 `9000`，只改映射左侧，并同步修改 `.env`：

```yaml
ports:
  - "192.168.1.20:9000:8080"
```

```dotenv
GWASM_ORIGIN=http://192.168.1.20:9000
```

容器内部端口仍保持 `8080`，除非同时修改 Compose 命令中的 `--port` 和健康
检查地址。

### 4.2 校验并启动

先让 Compose 展开环境变量并检查最终配置：

```bash
docker compose config
```

如果 `.env` 缺少 `GWASM_ORIGIN`，该命令会直接报错。确认输出中的镜像、端口和
`--origin` 正确后启动：

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 gwasm
```

然后在可信设备上打开 `.env` 中的 URL。

### 4.3 判断服务是否真正就绪

Compose 自带的健康检查只访问 `/`。它表示 HTTP 进程存活，不表示客户端文件或
完整镜像已经下载完成。首次启动时，容器可能已经显示 `healthy`，页面仍在下载。

查看下载状态：

```bash
curl -fsS http://192.168.1.20:8080/progress.json
```

典型阶段包括 `starting`、`client`、`image`、`ready` 和 `error`。只有
`phase` 为 `ready`、`done` 为 `true` 且 `error` 为空时，客户端才具备启动条件。
不依赖宿主机 Python 的就绪检查可以直接在容器内执行：

```bash
docker compose exec -T gwasm python -c \
  "import json,urllib.request; p=json.load(urllib.request.urlopen('http://127.0.0.1:8080/progress.json')); print(p); raise SystemExit(0 if p.get('phase') == 'ready' and p.get('done') else 1)"
```

第一次运行会先下载约 8 MB 的客户端，再按实际访问流式获取游戏数据。一次典型
启动的工作集约为 360 MB；后续运行会复用命名卷中的缓存。

## 5. 从源码构建容器镜像

需要部署未发布改动、规避旧镜像架构限制或审计构建内容时，在仓库根目录构建：

```bash
docker build --pull --tag gwasm:local .
```

新建一个不提交到仓库的 `compose.local.yaml`：

```yaml
services:
  gwasm:
    image: gwasm:local
    pull_policy: never
```

用两个 Compose 文件启动，后一个文件覆盖仓库默认的 GHCR 镜像和拉取策略：

```bash
docker compose -f compose.yaml -f compose.local.yaml config
docker compose -f compose.yaml -f compose.local.yaml up -d
```

使用 override 文件后，后续 `pull`、`up`、`logs`、`stop` 和 `down` 等命令都应
带上同一组 `-f` 参数，否则 Compose 会退回仓库默认的 GHCR 镜像配置。

代码变更后重新执行 `docker build`，再运行：

```bash
docker compose -f compose.yaml -f compose.local.yaml up -d --force-recreate
```

镜像以 Debian 的 `python:3.13-slim` 为基础，进程使用非 root 用户 `gwasm`，
只把运行时脚本、harness、图片和字体复制到 `/app`。游戏二进制和缓存不会进入
镜像，而是在运行时写入 `/data`。

## 6. 运行模式与配置

### 6.1 按需流式与完整镜像

默认模式只下载启动和当前操作实际需要的分块，首次等待较短，占用空间较少。
下载过的分块保存在 `/data/cache`。

如果希望提前下载完整约 4.2 GB 的游戏镜像，在 `compose.yaml` 的 `command`
列表末尾增加：

```yaml
      - --image
```

重新创建容器：

```bash
docker compose up -d
docker compose logs -f gwasm
```

完整下载大约需要 20 分钟，实际取决于网络时延。下载可恢复，中断后再次启动只会
获取缺失或校验失败的分块。程序会在下载前检查可用空间，并额外保留约 512 MB
余量。并发数默认和上限都是 8；不要绕过上限，因为所有安装共享 ArenaNet 客户端
访问密钥，提高并发可能影响其他用户。

完整缓存成功后，可以增加 `--offline` 禁止任何补下载。缓存不完整时，离线模式下
缺失分块会返回 HTTP 503，因此不要把 `--offline` 当作首次部署选项。

### 6.2 构建类型

| 参数 | 适用场景 | 说明 |
|---|---|---|
| `--build jspi` | Chrome/Edge，默认 | 客户端约 8.2 MB |
| `--build asyncify` | Safari/iOS | 客户端约 27.8 MB |
| `--build both` | 同时服务多类浏览器 | 下载两种构建，由浏览器选择 |

在 Compose 中修改现有 `--build` 参数或在 `command` 列表末尾添加参数和值即可。
改变构建类型后重新运行 `docker compose up -d`。

### 6.3 常用启动参数

| 参数 | 默认值 | 部署含义 |
|---|---|---|
| `--bind` | `127.0.0.1` | 容器中由 Compose 显式设为 `0.0.0.0` |
| `--port` | `8080` | 容器内部监听端口 |
| `--origin` | 本机 localhost Origin | 可重复；局域网部署必须显式配置 |
| `--cache` | 脚本旁的 `gwpatch-cache/` | Compose 使用 `/data/cache` |
| `--dir` | 脚本旁的 `dist/` | Compose 使用 `/data/dist` |
| `-j`, `--jobs` | `8` | 下载并发，允许范围 1 到 8 |
| `--image` | 关闭 | 启动前获取完整 4.2 GB 镜像 |
| `--offline` | 关闭 | 禁止下载，缓存缺失时返回 503 |
| `--no-update` | 关闭 | 跳过客户端版本检查 |
| `--no-prefetch` | 关闭 | 不预热上次记录的启动工作集 |
| `--no-browser` | 关闭 | Compose 必须启用，浏览器在宿主设备上打开 |
| `-v`, `--verbose` | 关闭 | 记录每个 HTTP 请求，排障时短期开启 |

`-v` 会为一次启动产生大量 Range 请求日志，不应长期启用。

### 6.4 环境变量与安全敏感开关

`.env` 中的 `GWASM_ORIGIN` 是 Compose 插值变量，不是程序直接读取的环境变量；
Compose 会把它转换为 `--origin` 参数。其余变量只有显式写入服务的
`environment:` 后才会进入容器。

| 变量 | 默认值 | 风险 |
|---|---|---|
| `GWASM_RELAY_DOMAINS` | `arenanetworks.com,guildwars.com` | 扩大 `/dns` 可解析域名范围 |
| `GWASM_RELAY_PORTS` | `6112,80,443` | 扩大 relay 可连接的目标端口 |
| `GWASM_RELAY_ALLOW_PRIVATE` | 未设置 | 设为 `1` 会允许访问私网、回环和链路本地地址 |
| `GWASM_PROXY_ROUTES` | 内置 Web 服务映射 | 改写同源 API proxy 的上游主机 |
| `GWASM_PROXY_TEMPLATE` | 未设置 | 测试夹具使用，正常部署不应设置 |

不要在正常部署中设置 `GWASM_RELAY_ALLOW_PRIVATE=1`。它会削弱阻止 SSRF、局域网
扫描和云元数据访问的关键边界，仅用于离线 E2E 测试。除非明确理解 relay 的
安全模型，也不要扩大域名和端口白名单。

## 7. 不使用容器运行

从 GitHub Releases 下载最新 `gwasm-<版本>.zip`，完整解压后进入目录。

仅供当前电脑使用：

```bash
python3 gwasm.py
```

程序监听 `127.0.0.1:8080`、自动打开浏览器，并在最后一个页面关闭后停止。
数据写入解压目录中的 `dist/` 和 `gwpatch-cache/`。

在可信局域网中手动运行：

```bash
python3 gwasm.py \
  --no-browser \
  --bind 0.0.0.0 \
  --port 8080 \
  --origin http://192.168.1.20:8080
```

此模式下不会启用“最后一个浏览器标签关闭后自动退出”的 watchdog，需要用
Ctrl+C 或进程管理器停止服务。SIGTERM 会触发有序关闭，缓存下载和文件组装采用
临时文件加原子替换，中断后可以继续。

## 8. 数据持久化与备份

Compose 的命名卷 `gwasm-data` 挂载到 `/data`：

```text
/data/cache  内容寻址的游戏分块、启动工作集记录
/data/dist   客户端 JS/Wasm、静态页面和 snapshot 索引
```

`docker compose stop`、`restart`、`up -d` 和 `down` 都保留该卷。以下命令会
删除数据卷并导致重新下载，请只在明确需要清空缓存时执行：

```bash
docker compose down -v
```

查看数据量：

```bash
docker compose exec gwasm sh -c 'du -sh /data/cache /data/dist'
```

### 8.1 备份

缓存全部可以从上游重建，通常不必备份。如果完整镜像下载成本较高，可以先停止
服务，再把命名卷归档。实际卷名受 Compose project name 影响，先从容器挂载信息
中取得：

```bash
mkdir -p ../gwasm-backups
BACKUP_DIR=$(cd ../gwasm-backups && pwd)
VOLUME=$(docker inspect "$(docker compose ps -q gwasm)" \
  --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')
test -n "$VOLUME"
docker compose stop
docker run --rm \
  -v "$VOLUME:/data:ro" \
  alpine:3.22 \
  tar -czf - -C /data . > "$BACKUP_DIR/gwasm-data.tar.gz.part" && \
  mv "$BACKUP_DIR/gwasm-data.tar.gz.part" "$BACKUP_DIR/gwasm-data.tar.gz"
docker compose start
```

归档中包含从 ArenaNet 下载的游戏数据，只应用于个人恢复或迁移，不要把它提交到
仓库、附加到 Release 或公开分发。

### 8.2 恢复

恢复前停止服务，并确认当前目录中的归档名称正确：

```bash
BACKUP_DIR=$(cd ../gwasm-backups && pwd)
test -s "$BACKUP_DIR/gwasm-data.tar.gz"
docker compose down
docker compose create
CONTAINER=$(docker compose ps -aq gwasm)
VOLUME=$(docker inspect "$CONTAINER" \
  --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')
IMAGE=$(docker inspect "$CONTAINER" --format '{{.Image}}')
OWNER=$(docker run --rm --entrypoint sh "$IMAGE" \
  -c 'printf "%s:%s" "$(id -u)" "$(id -g)"')
test -n "$VOLUME" && test -n "$OWNER"
docker run --rm -i \
  -v "$VOLUME:/data" \
  alpine:3.22 \
  tar -xzf - -C /data < "$BACKUP_DIR/gwasm-data.tar.gz"
docker run --rm \
  -v "$VOLUME:/data" \
  alpine:3.22 \
  chown -R "$OWNER" /data
docker compose up -d
```

`docker compose create` 会在新主机上创建目标命名卷和一个未启动的容器，因此
不依赖目录名猜测卷名。恢复命令还会把文件所有者调整为目标镜像中的非 root 用户，
避免不同镜像版本的 UID/GID 变化导致缓存不可写。使用 local 或 pin override 时，
这一节的 Compose 命令也要带相同的 `-f` 参数。恢复后通过日志和
`/progress.json` 验证文件校验与就绪状态。

## 9. 日常运维

### 9.1 状态、日志和重启

```bash
docker compose ps
docker compose logs --tail=200 gwasm
docker compose logs -f gwasm
docker compose restart gwasm
```

正常启动日志至少会显示：服务目录和 URL、监听地址、允许的 Origin、relay 域名和
端口，以及 snapshot 已缓存分块数量。错误请求即使未启用 `-v` 也会记录。

Compose 设置了 `restart: unless-stopped`。Docker 守护进程或主机重启后，容器会
自动恢复；人工停止的容器不会自动启动。停止时 Compose 最多等待 30 秒，程序收到
SIGTERM 后会停止后台下载、关闭 HTTP 服务并保存已记录的工作集。

### 9.2 升级

仓库默认 Compose 使用可变的 `latest` 标签和 `pull_policy: always`。每次运行
`docker compose up -d` 都可能拉取新镜像，因此升级前应先阅读 Release 说明，并
记录当前镜像摘要：

```bash
docker compose images
docker inspect "$(docker compose ps -q gwasm)" \
  --format 'configured={{.Config.Image}} image-id={{.Image}}'
docker compose pull
docker compose up -d
```

升级不会删除命名卷。升级后检查容器状态、日志和 `/progress.json`，再从浏览器
完成一次加载验证。

### 9.3 固定版本与回滚

需要稳定部署和方便回滚时，建议使用 Release 标签而不是 `latest`。新建
`compose.pin.yaml`，将 `<release-tag>` 替换为真实标签，例如 Release 页面中的
`vYYYY.MM.DD.N`：

```yaml
services:
  gwasm:
    image: ghcr.io/zhubby/gwasm:<release-tag>
    pull_policy: missing
```

部署固定版本：

```bash
docker compose -f compose.yaml -f compose.pin.yaml pull
docker compose -f compose.yaml -f compose.pin.yaml up -d
```

固定版本后，所有运维命令都要继续带这两个 `-f` 参数，否则会重新使用默认的
`latest`。也可以在部署系统中统一设置 Compose 文件列表，避免人工漏写。

容器标签在技术上仍可被重新指向。需要字节级可重复部署时，先用
`docker buildx imagetools inspect` 记录对应 Release 的 manifest digest，再把
override 中的镜像写成摘要形式：

```yaml
services:
  gwasm:
    image: ghcr.io/zhubby/gwasm@sha256:<manifest-digest>
    pull_policy: missing
```

回滚时只需把 `compose.pin.yaml` 改回上一个已验证标签，再运行同一组命令。数据卷
通常可以直接复用，因为其中只有可重建的客户端文件和内容寻址分块；如果旧版本
无法读取新生成的索引，可先备份，然后删除卷让旧版本重建缓存。

## 10. 安全加固

默认实现已经包含非 root 容器、精确 Origin 检查、目标域名和端口白名单，以及
仅允许公网单播目标地址的 relay 规则。部署者仍需完成外围限制：

1. 只在可信局域网运行，并用主机防火墙限制 TCP 8080 的来源网段。
2. 尽量把 Compose 端口绑定到单个 LAN IP，而不是 `0.0.0.0`。
3. 保持 `GWASM_ORIGIN` 与唯一入口完全一致，不使用通配符。
4. 不设置 `GWASM_RELAY_ALLOW_PRIVATE=1`，不随意扩大 relay 域名和端口。
5. 不把 Docker socket、宿主目录或凭据挂载进容器。
6. 不在日志、备份或 Release 中发布下载后的客户端、snapshot 和分块缓存。

如需在可信私网中通过现有反向代理提供 HTTPS，应把容器端口只绑定到代理可访问的
本机接口，例如 `127.0.0.1:8080:8080`，并将 `GWASM_ORIGIN` 设置为浏览器看到的
外部 HTTPS Origin（包括端口）。代理必须支持 WebSocket Upgrade，并保持浏览器的
`Origin` 头；页面的 CSP 会为 HTTPS Origin 生成对应的 `wss://` 连接地址。

反向代理和 TLS **不会**为 gwasm 增加用户认证或多租户隔离，因此这仍不是把服务
开放到公网的充分条件。

## 11. 故障排查

### `docker compose config` 提示必须设置 `GWASM_ORIGIN`

确认仓库根目录有 `.env`，变量名拼写正确，且运行命令时的工作目录是该仓库。

### 容器启动失败并提示端口已占用

查看占用情况，或把 Compose 端口映射左侧改为其他端口。改为 `9000` 时必须同步
把 `GWASM_ORIGIN` 改为浏览器实际访问的 `http://主机:9000`。

### 页面能打开，但 WebSocket 或 API 返回 403

这通常是 Origin 不匹配。检查协议、主机名/IP 和外部端口；`localhost`、LAN IP
和 DNS 主机名是三个不同 Origin。不要在值末尾加 `/`。

### 容器是 healthy，但页面仍在加载

这是当前健康检查的预期语义：它只表示 HTTP 服务存活。查看
`/progress.json` 和 `docker compose logs -f gwasm`；首次运行需要下载和组装客户端。

### `/progress.json` 的阶段为 `error`

先看容器日志中的具体上游错误，再检查 DNS、HTTPS 出站访问、系统时间和磁盘空间。
重新启动不会丢失已经通过哈希校验的分块。

### ARM 主机显示 `no matching manifest`

该版本镜像可能只发布了 `linux/amd64`。检查镜像清单，升级到包含
`linux/arm64` 的版本，或在 ARM 主机按[从源码构建容器镜像](#5-从源码构建容器镜像)
构建本地镜像。强制 `linux/amd64` 可以通过模拟临时运行，但性能和可预测性较差。

### 离线模式访问 snapshot 返回 503

所需分块尚未缓存。移除 `--offline` 允许按需下载，或在线运行一次 `--image` 并等
待 `phase=ready` 后再切换为离线模式。

### Safari/iOS 无法启动客户端

默认部署只下载 JSPI 构建。把 `--build` 改为 `asyncify`，或为混合浏览器环境使用
`--build both`，然后重新创建容器。

### 下载完成后页面空白

确认 `/progress.json` 已 ready，并在近期 Chrome/Edge 上重试。真实 GPU 渲染目前
仍是项目的未验证项；提交问题时应附上操作系统、浏览器版本、GPU 型号和浏览器
控制台日志。

### 无法登录

这不是部署故障。项目尚未实现账号认证和安全凭据存储，当前版本不能登录游戏。

## 12. 停止与卸载

停止但保留容器和缓存：

```bash
docker compose stop
```

删除容器和网络但保留缓存：

```bash
docker compose down
```

删除容器、网络和全部缓存：

```bash
docker compose down -v
```

最后一条命令不可从 Docker 中恢复，下一次启动需要重新下载客户端和游戏数据。

## 13. 部署验收清单

- `docker compose config` 展开的 URL、端口和 Origin 正确；
- 端口只暴露给预期的可信网段；
- `docker compose ps` 显示容器运行且健康；
- `/progress.json` 返回 `phase=ready`、`done=true`、`error=null`；
- 浏览器从与 `GWASM_ORIGIN` 完全一致的 URL 打开页面；
- 日志中没有 Origin 拒绝、DNS、proxy、磁盘空间或哈希校验错误；
- 重建容器后 `/data` 缓存仍然存在；
- 已记录当前部署的 Release 标签或镜像摘要，具备明确的回滚目标。

## 14. 维护者发布说明

`.github/workflows/release.yml` 只在 GitHub Release 发布时运行。发布流水线会先执行
Python 3.8/3.13 测试、无泄漏检查、JavaScript harness 测试、Chromium E2E 和容器
smoke test；全部通过后才上传可运行 zip，并向 GHCR 发布 Release 标签和 `latest`。

发布后至少验证：

```bash
gh release view <release-tag> --repo zhubby/gwasm
docker buildx imagetools inspect ghcr.io/zhubby/gwasm:<release-tag>
docker run --rm ghcr.io/zhubby/gwasm:<release-tag> --help
```

Release zip 和容器镜像都不应包含 `.wasm`、APK、snapshot、缓存分块或用户凭据；
这些边界由 `tests/test_no_leaks.py` 和容器 smoke test 共同守护。

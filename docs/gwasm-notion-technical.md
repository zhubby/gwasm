---
title: gwasm：在普通浏览器中运行 Guild Wars WebAssembly 客户端的技术实现
---

> **日期快照（2026-10-25）：** 本文保留当时的研究结论，不再作为目录和
> CI 细节的权威来源。持续维护的实现说明见
> [docs/internals.md](internals.md)。

## 结论先行

这个仓库不是重新实现游戏，而是把移动版 Guild Wars 客户端暴露出来的 WebAssembly 模块，放进一个普通浏览器可以提供的宿主环境中。整体链路如下：

```text
ArenaNet Patch CDN
        |
        | manifest + content-addressed chunks
        v
gwasm.py + gwasm_core/（Python 3.8+ 标准库）
  | HTTP 静态文件 / Range / 虚拟 Gw.snapshot
  | WebSocket -> TCP relay / DNS / Web API proxy
  v
harness/index.html + 按序加载的 harness-*.js
  | Module.image / socket / dns / storage / EGL / input
  v
Emscripten glue + Gw.jspi.wasm / Gw.wasm
```

当前实现已经能下载并启动客户端、按需读取 4.2 GB 游戏快照、解析并连接 ArenaNet 的内容服务器，也能完成认证前的网络探测。它仍然不能真正登录：login、nativeAccount 和部分账户相关能力没有接入真实凭据。渲染链路在无 GPU 的 headless Chromium/SwiftShader 上会在 GLES 3 shader 编译阶段中止，因此真实硬件上的画面是否正常仍需验证。

## 1. 技术基线与目录职责

项目是一个无依赖的 Python + JavaScript + WebAssembly 工具，没有数据库、Node 构建链或后端框架。Python 运行时只依赖标准库；Node/Chromium 只在测试阶段使用。

| 路径 | 职责 |
| --- | --- |
| gwasm.py | 轻量 CLI、安装诊断和线程生命周期编排 |
| gwasm_core/ | 共享 HTTP、patch/chunk、runtime 和 server 实现 |
| harness/ | 浏览器页面和 Module 宿主实现；负责图像快照、socket、DNS、存储、EGL、输入和加载进度 |
| gwpatch.py / getsnapshot.py | 独立的补丁协议客户端和快照下载/组装工具 |
| gwkey.py | 从 APK 中提取公开的客户端 X-Access-Key |
| tools/ | WebAssembly 解码、扫描、表扩容、静态 detour、符号恢复 |
| tests/ | Python 服务器/存储测试、JavaScript harness 契约测试、浏览器 E2E、发布泄漏检查 |
| .github/workflows/ci.yml | PR/main 与 release 共用的 Python、宿主、Chromium 和容器验证 |
| .github/workflows/release.yml | 复用 CI 后打包 zip，并发布 AMD64/ARM64 容器 |

一个重要的工程约束是“零依赖”：gwasm.py 与相邻的 gwasm_core/ 可在系统
Python 上直接运行，不需要 pip install；release zip 会同时包含两者。

## 2. 启动和更新流程

### 2.1 先启动页面，再在后台更新

gwasm.py 的 main() 先确定自身目录、检查 Python 版本和 harness 必需文件，再把 harness/、images/、fonts/ 发布到 dist/。随后立即启动 ThreadingHTTPServer，把下载工作放到后台线程；浏览器先打开加载页，用户看到的是进度，而不是一个无响应的终端窗口。

Watchdog 通过页面的 /ping 和离开 beacon 维护 tab 状态：

- 页面关闭后等待 GRACE=3s，避免 reload/navigation 误杀服务器；
- 后台标签页的计时器会被 Chrome 降频，所以无 goodbye 时用 IDLE=150s 兜底；
- tab 使用独立 id，避免“先发 goodbye、后到达的普通 ping”重新把已离开的 tab 标记为存活；
- 最后一个 tab 消失后调用 httpd.shutdown()，不留下后台进程。

### 2.2 manifest 驱动的内容寻址更新

补丁 CDN 的 manifest.json 记录 compressionMode、chunkSize、目录树和文件的 chunk hash 列表。Manifest 将 directories[] 的 parentIndex 展平为路径，并验证文件大小对应的 chunk 数量，拒绝未知压缩模式或不一致的 manifest。

需要的客户端文件由 --build 决定：

- JSPI：Gw.jspi.js + Gw.jspi.wasm，约 8.2 MB；
- Asyncify：Gw.js + Gw.wasm，约 27.8 MB；
- 另加 version.json。

每个 chunk 以 hash 作为文件名保存在 gwpatch-cache/。下载后先校验 MD5/SHA-1/SHA-256（由 hash 长度决定），写到临时文件，再通过 os.replace() 原子落盘，避免中断留下半个 chunk。客户端文件只有在所有 chunk 准备好后才组装到 dist/，因此更新可恢复，也只重新下载发生变化的内容。

快照 Gw.snapshot 约 4.2 GB，但 gwasm.py 不组装它。更新阶段只写 dist/snapshot-chunks.json，内容包括总大小、chunk 大小、缓存目录和 chunk hash 列表；HTTP 层后续按 Range 从缓存拼接返回。

### 2.3 连接复用与并发上限

Http 为每个线程维护 (scheme, host) 连接池，避免大量小 chunk 请求反复建立 TCP/TLS。临时网络错误采用指数退避；401/403/404 被视为不可通过重试解决的拒绝。

默认 PREFETCH_JOBS=8。单请求受约 650 ms 往返延迟限制，并发能近似线性提高吞吐，但同一 ACCESS_KEY 被所有安装共享，过高并发会把 CDN 的压力放大到所有用户，因此代码和文档都把 8 视为有意的服务保护上限。

## 3. 4.2 GB 快照的数据平面

这是仓库中最关键的性能设计：把“文件系统式的随机读取”转换成“可验证、可缓存、可并发的分块读取”。

### 3.1 服务端 ChunkStore

ChunkStore.read(start, length) 逐 chunk 计算覆盖范围，从内容寻址缓存读取并拼接字节。未缓存 chunk 通过 CDN hash URL 拉取，并在成功后进入本地缓存。

并发读相同 chunk 时，_locks 按 hash 建立锁：不同 chunk 可并发下载，同一个 chunk 只会真正请求一次。测试中 6 个并发 reader 对同一未缓存 chunk 最终只有一次网络请求。

每次访问还会记录 chunk index 到 boot-chunks.json。记录采用合并写入而不是替换，防止一次提前中断的启动把之前完整启动积累的工作集缩小。下一次运行用 prefetch() 并发预热已知工作集；预热失败不会阻止游戏自己按需请求。

--image 切换到完整下载模式：

1. 先统计缺失 chunk 和磁盘剩余空间，至少预留 512 MiB 余量；
2. 并发下载所有缺失 chunk，按实际字节数更新速率和 ETA；
3. 中断后下次只继续缺失项；
4. 不组装文件也可以由服务端直接提供，只有显式 --out 的独立工具才会组装完整快照。

### 3.2 浏览器端两级缓存和请求去重

harness-cache.js 在服务端 chunk 之上再做两级缓存：

- 内存 Map 作为 256 MiB LRU，键为 chunk index；
- Cache API 作为跨 reload 的持久层，键中包含内容 hash，因此快照更新时不会误用旧数据。

chunkBytes(i) 的核心是 inflight Map：每个 chunk index 同时只保留一个 Promise，重叠的 cacheAsync/readAsync 共享该 Promise。代码注释给出了实测对比：没有去重时一次启动产生 3557 次请求、只覆盖 886 个不同 chunk，传输 932 MB 才交付 227 MB；去重后重复下载被消除，也降低了对生产 CDN 的压力。

image.readAsync() 先尝试从内存组装跨 chunk 的范围，缺失时拉取覆盖范围的完整 chunk，然后把结果写入 Emscripten 的 Module.HEAPU8。image.fileSize() 必须同步返回，因此 harness 在 glue 加载前读取 snapshot-chunks.json，这也是为什么快照索引不是可选的运行时细节。

isCached() 不能等待 Promise，所以它同步检查内存缓存和 storedHashes。网络写入 Cache API 是乐观记录，写失败会撤销 hash，避免把未持久化的数据报告成已缓存。

## 4. Emscripten 宿主接口：让同一个 wasm 在浏览器里运行

移动客户端原本在 Capacitor WebView 中运行，平台能力以 Module.* 对象注入。普通浏览器只要提供同样的接口即可复用 wasm 核心。

### 4.1 Module 的初始化契约

Emscripten glue 开头使用 var Module = typeof Module != 'undefined' ? Module : {}。因此 harness 必须用顶层 var Module; 并随后赋值；若使用 const/let，会与 glue 的 var 在同一全局脚本作用域冲突，或者让 glue 看不到宿主对象。tests/test_harness.js 专门覆盖这两个失败模式。

| Module 对象 | 实现要点 |
| --- | --- |
| image | open/fileSize/close/readAsync/isCached/cacheAsync；只对 Gw.snapshot 返回有效句柄 |
| socket | 将 connect(destAddr) 映射到同源 WebSocket，回调 payload 直接传给 wasm |
| dns | resolve(name) 请求同源 /dns |
| secureStorage | 三个 async 方法完整实现；凭据只保存在当前页面生命周期的内存中 |
| login | hasProvider() 全部返回 false，未接 federated login |
| shop/adProvider/browser/events/ageSignals | Proxy stub；所有被 glue 触达的属性都保持 callable |
| getPatchMode | onDemand/preload 从 localStorage 读取 |
| setStartupProgress | 把模块自身的连接、下载、解压、完成阶段映射到 loading UI |

有九个宿主方法会被 glue await 或调用 .then()：image.cacheAsync、dns.resolve、三个 secureStorage 方法、adProvider.showInterstitial、ageSignals.check、shop.initialize、shop.inAppPurchase。它们即使只是 stub 也必须返回 Promise；仅仅“可调用”会在启动中途触发 undefined.then。

### 4.2 JSPI/Asyncify 和 wasm 加载

页面根据设备和 WebAssembly.Suspending 能力选择构建：非 iOS 且支持 JSPI 时优先加载 Gw.jspi.js，否则使用 Asyncify。两个构建共享输出 basename，JSPI glue 默认还会寻找 Gw.wasm，因此 harness 用 locateFile() 把它重定向到 Gw.jspi.wasm；否则会静默配错二进制。

instantiateWasm() 在实例化前修改 imports.env，优先 WebAssembly.instantiateStreaming()，失败时再退回完整 arrayBuffer() 实例化。模块没有 atomics/SIMD/SharedArrayBuffer，不需要 COOP/COEP 隔离头。

### 4.3 EGL 到可见画布

wasm 通过 Emscripten 的 EGL/GLES shim 渲染。patchEgl() 在创建上下文时把 Module.canvas 临时替换为同尺寸的 OffscreenCanvas；交换缓冲时调用 transferToImageBitmap()，再由可见 canvas 的 bitmaprenderer transferFromImageBitmap() 呈现。尺寸变更会同步到 offscreen buffer，避免 CSS 尺寸和实际渲染尺寸分离。

headless Chromium 当前会在 GL ES 3.0 default vertex shader compilation failed 处退出，日志已经能走到 first frame presented。这说明 presentation wiring 被触发，但不能据此宣称真实 GPU 渲染已经通过。

### 4.4 输入和文本

桌面输入不是简单地把鼠标事件原样转给游戏：

- dbltap 模式保留普通鼠标事件，并在快速双击时延迟生成两个 touch tap；
- translate/augment 模式把左键流转换为 touch，右键仍用于相机；
- 右键拖动超过阈值后请求 Pointer Lock，用 movementX/Y 累积虚拟光标，鼠标释放或 blur 时解除锁定；
- 五个隐藏/模态 OSK 输入框对应 text/email/password/number/multiline，失焦保护避免输入框吞掉游戏键盘事件；
- 音频上下文只在 pointer/keyboard 用户手势后恢复，符合浏览器 autoplay 策略。

## 5. 本地网络中继与安全边界

浏览器不能直接打开原始 TCP，游戏协议又不是 HTTP，所以 gwasm.py 在同一端口上实现 WebSocket-to-TCP relay。使用同源端口有三个好处：不需要 CORS、不需要额外端口配置、页面和 relay 的生命周期一致。

### 5.1 WebSocket 握手顺序

Handler._websocket() 先校验 Origin、目标地址格式、可拨号 IP 和端口，再实际建立 TCP，最后才返回 101 Switching Protocols。这是有意的顺序：模块把 WebSocket onopen 当作“TCP 已建立”，如果先升级再拨号，模块会立即发送握手数据，而 TCP 尚未就绪，数据可能丢失并造成无休止重连。

桥接循环用 select.select() 同时监听浏览器 socket 和 TCP socket；浏览器来的帧按 RFC 6455 解 mask，发往浏览器的二进制帧不 mask。连接关闭时记录双向字节数，便于区分“模块没写数据”“对端在握手后关闭”和“协议层断开”。

### 5.2 DNS 和地址规则

/dns 只允许 arenanetworks.com 和 guildwars.com 的严格后缀匹配，拒绝 arenanetworks.com.evil.com 这类 lookalike。解析优先 getaddrinfo()，失败后读取 /etc/resolv.conf 并发送最小 UDP A-record 查询，最后尝试 1.1.1.1/8.8.8.8。这样可以处理 geodc.arenanetworks.com 返回 0.0.1.2 这种承载数据中心编号而非可拨号地址的记录。

TCP 目标只允许公共单播地址和端口 6112,80,443。loopback、RFC1918、CGNAT、link-local/云元数据地址、多播和保留网段都会被拒绝；IPv4-mapped IPv6 也会继续按 IPv4 规则判断。GWASM_RELAY_ALLOW_PRIVATE=1 只为 E2E 测试连接 loopback fixture，启动时会显式告警。

这里有一个容易误解的设计取舍：relay 不限制“只能连接自己刚刚 DNS 解析过的 IP”。游戏服务器地址来自登录响应，不一定经过 /dns，所以真正应该约束的是目标地址能否被网页直接访问，而不是是否由 relay 解析过。

### 5.3 Web API 代理

客户端在脱离 Capacitor 后会把部分 API 请求改写为自身 origin，但丢失端口并只保留第一段 hostname。harness 重写 XHR 把默认的 80/443 端口补回当前本地端口；gwasm.py 再按 route 映射到 webgate.ncplatform.net、account.arena.net、help/store/www.guildwars.com 等真实 Web 服务。未知 route 明确返回 502，不猜测域名。代理会过滤 hop-by-hop headers，并保留上游 401 等真实业务响应。

## 6. WebAssembly 离线分析工具链

tools/ 不依赖运行中的游戏进程，直接把 wasm 二进制当作事实来源。因为 wasm 的 code section 不在线性内存里，逆向分析必须在模块字节上进行。

1. wasmscan.py 实现完整指令解码，解析 imports、functions、data segments、indirect table，并建立 i32.const 数据地址到函数的引用索引；可用字符串和源文件路径定位函数。
2. 常量必须解码而不能字节匹配：LLVM 生成的 relocatable LEB128 可能是固定宽度、零填充的非 canonical 编码。
3. wasmpatch.py 把所有函数追加到 indirect table，并预留增长空间；原始模块只把 address-taken 函数放进表，且 min == max 会让 side module 的 table.grow 失败。
4. wasmdetour.py 把原函数体搬到 gwca_orig_<f>，原位置替换为读取 hook table 的 dispatcher，从而能拦截 direct call，并导出 gwca_hook_base 与 gwca_orig_slot_base 供运行时发现布局。
5. gensyms.py 从 imports/exports 和 .data 中保留的 850 个源路径恢复符号，写入标准 name custom section，同时生成字符串交叉引用 CSV 和 Ghidra 导入脚本。

这套工具把“没有符号的 17k 函数 wasm”变成可定位、可 patch、可被 Ghidra/DevTools 识别的分析对象，且输出都落在 gitignored 的 build/，不会把游戏二进制带入仓库。

## 7. 测试、CI 与可验证范围

测试策略很清晰：所有可离线验证的行为用本地 fixture 覆盖，真实 CDN/登录/硬件则不在单元测试中伪装成已通过。

| 命令 | 覆盖内容 | 本次结果 |
| --- | --- | --- |
| python3 -m unittest discover -s tests -p 'test_*.py' | tools、patch、runtime、server 隔离和发行边界 | 通过 |
| python3 tests/test_no_leaks.py | 阻止 .apk、.wasm、快照、manifest、依赖目录和意外 credential 进入 git | 通过，0 failure |
| node tests/test_harness.js | Module adoption、宿主方法集合、socket 回调形状、stub/账户接口 | 通过，0 failure |
| node tests/e2e.js | 用真实 harness + 本地合成快照 + 本地 TCP fixture 驱动浏览器 | 通过，包含真实 WebAssembly 编译检查 |

release workflow 复用 PR/main 的完整 CI，然后从 checkout 打包 gwasm.py、
gwasm_core/、harness/、images/、fonts/、README 和 LICENSE；最后解压 zip
再运行 gwasm.py --help 并检查核心模块和全部宿主脚本。

## 8. 当前限制与工程风险

1. **认证尚未实现。** 这是从“能启动”到“能玩”的主要阻塞点。接入账号凭据会把项目从互操作研究推进到真实账户流量处理，不能简单地把当前 stub 改成日志或硬编码密码。
2. **浏览器端 secure storage 不是持久安全存储。** 当前凭据只保留在页面内存中，刷新或关闭页面即丢失；真实版本仍需要原生辅助进程或等价的隔离存储，不能把浏览器内存当作移动端 Keychain 等价物。
3. **渲染未在真实 GPU 验证。** SwiftShader 的 shader 编译失败可能是 headless 环境特性，也可能暴露真实兼容性问题；需要在 Chrome/Firefox、集成显卡和独立显卡上记录 WebGL/GLES 日志。
4. **持久 chunk cache 的命中率有限。** 文档记录过约 360 MB 附近的 quota/persistence 疑点；需要进一步测量 Cache API 配额、navigator.storage.persist() 结果和跨 reload 的工作集命中率。
5. **relay 是本地受限 open proxy。** 默认绑定 127.0.0.1、Origin、域名、端口和公共地址规则共同构成边界；任何扩大 GWASM_RELAY_DOMAINS、GWASM_RELAY_PORTS 或设置 GWASM_RELAY_ALLOW_PRIVATE=1 的部署都应视为安全决策，而不是普通调试开关。
6. **生产 CDN 访问具有外部影响。** 共享 access key 和并发下载会影响真实服务，默认 8 worker、指数退避、清晰 User-Agent 是协议互操作之外的运维约束。

## 9. 可复用的设计经验

- 对超大随机访问对象，优先保存“manifest + 内容寻址 chunk”，不要强迫首次运行组装完整文件；这样可以把启动工作集从 4.2 GB 降到几十个 chunk。
- 对异步宿主接口，先从 glue 的 await/.then() 调用点反推契约，再写 stub；“函数存在”不等于“Promise 形状正确”。
- 把并发去重放在最接近数据源的位置：服务端按 hash 加锁、浏览器端按 chunk 共享 Promise，两层都避免重复放大。
- 同源 relay 的安全边界要同时检查地址、端口、Origin 和域名；只做域名 allowlist 或只做“我解析过的 IP”都不足以覆盖真实客户端行为。
- 对浏览器生命周期、标签页降频、reload 竞态等状态问题，身份和显式状态比单一时间戳可靠。
- 把“真实 CDN/硬件/账号”与离线 fixture 测试分开，明确哪些结论是已验证事实，哪些仍需现场实验。

## 10. 推荐的后续工作顺序

1. 先在真实 GPU 上验证 EGL/OffscreenCanvas 呈现，收集不同浏览器和驱动的 shader 错误。
2. 在不暴露明文凭据的前提下设计认证适配层和失败恢复流程，明确账号流量、日志脱敏和退出清理策略。
3. 对 Cache API/IDBFS 做配额和持久化实验，决定是否需要按工作集淘汰、用户可见清理或文件系统后端。
4. 保持 Playwright 与 Chromium 版本固定，使 tests/e2e.js 在贡献者机器和 CI 中可重复运行。
5. 继续使用 tools/ 对新客户端版本做 manifest、import、host method 和函数布局差异检查，避免版本更新后出现“页面能打开但 wasm 在中途崩溃”的隐性回归。

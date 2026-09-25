# duoChrome

轻量级多账号浏览器隔离工具 —— 类 AdsPower / BitBrowser，但只保留你最需要的那部分。

> Open N independent Chromium windows, each with its own cookies / cache / storage.
> Same site, different accounts, simultaneously.

## 为什么需要这个

你打开抖音创作者中心，要同时登录 3 个蓝 V 账号——同一个 Chrome 多 tab 是不行的（cookies 互相覆盖）。
手动开多个 Chrome profile 又麻烦（每次新建 profile、重新登录）。
商业工具（比特浏览器 / AdsPower / 候鸟）能做这事，但要么收费、要么太重。

duoChrome 是中间方案：**轻量到 500 行 Python，但能稳定开 N 个独立 Chrome 实例**。

## 它做了什么 / 不做什么

✅ **做**
- 每个 profile = **独立 OS 进程 + 独立 user-data-dir**（cookies / cache / IndexedDB 物理隔离）
- CLI 管理 profile 列表（create / ls / launch / stop / rm）
- 按 group 批量启动
- 可选代理（http / socks5）+ 自定义 UA + viewport
- 基础 fingerprint patch（`navigator.webdriver` 等 4 处最常被检测的字段）

❌ **不做**（v0.1）
- 深度指纹伪装（Canvas / WebGL / Audio 等）—— 用 [playwright-stealth](https://github.com/AtuboAt/playwright-stealth) 补
- Web UI / GUI —— 先 CLI 起，等需求再加
- Cookie 跨浏览器导入 —— v0.2，按需做
- 云端同步 / 团队协作 —— 这是商业产品的范畴

## 安装

```bash
git clone <this-repo> duoChrome
cd duoChrome
pip install -e .
playwright install chromium   # 首次需要装浏览器
```

## 快速开始

### CLI

```bash
# 1. 初始化（创建 ~/.duochrome/）
duochrome init

# 2. 创建几个 profile
duochrome create 门店A-账号1 --group 门店A
duochrome create 门店A-账号2 --group 门店A --proxy socks5://127.0.0.1:1080
duochrome create 门店B-账号1 --group 门店B --ua "Mozilla/5.0 (custom)"

# 3. 看一眼
duochrome ls
# NAME              GROUP   PROXY                       ALIVE  LAST_OPENED
# ----------------  ------  -------------------------- -----  -------------------
# 门店A-账号1       门店A    -                           ○      -
# 门店A-账号2       门店A    socks5://127.0.0.1:1080     ○      -
# 门店B-账号1       门店B    -                           ○      -

# 4. 启动（每个会开一个独立 Chrome 窗口）
duochrome launch 门店A-账号1

# 5. 一组一起启动（适合"多账号同网站"场景）
duochrome launch --group 门店A

# 6. 关掉
duochrome stop 门店A-账号1
duochrome stop --all

# 7. 删除（含所有 cookies / cache）
duochrome rm 门店A-账号1 --yes
```

### Python

```python
from duochrome import DuoChrome

dc = DuoChrome()                       # 默认根目录 ~/.duochrome
dc.init()

# 建 profile
if not dc.store.exists("账号A"):
    dc.create("账号A", group="抖音", proxy="socks5://127.0.0.1:1080")

# 启动 → 拿到 playwright BrowserContext
ctx = dc.launch("账号A", url="https://creator.douyin.com")
page = ctx.pages[0]
# ... 你的自动化逻辑 ...

# 进程退出时自动清理；想立刻关就 ctx.close()
```

跑完整 example：
```bash
python examples/basic.py
```

## 设计原理（每个 instance 是独立的）

`launch_persistent_context(profile_dir)` 每次调用都做 4 件事：

1. **新 OS 进程**：Playwright 拉起一个独立的 Chromium 子进程（不是 tab）
2. **profile_dir 物理隔离**：cookies / cache / IndexedDB / Service Worker 各自落自己的目录
3. **持久化**：浏览器关掉再开，数据还在
4. **完全独立**：A profile 的 session 跟 B profile 互不可见

也就是说你可以开 10 个 Chrome 窗口，分别登录 10 个抖音账号——互不串。

## 配置文件 / 数据目录

```
~/.duochrome/
├── profiles.json                  # profile 元数据索引
└── profiles/
    ├── 门店A-账号1/
    │   ├── chrome/                # 真实 Chromium user-data-dir
    │   └── .pid                   # 上次启动的 PID
    └── 门店A-账号2/
        ├── chrome/
        └── .pid
```

自定义 root：`duochrome --root /path/to/root ls`

## Roadmap

- [ ] **cookie 导入**：从小V猫 partition / 本机 Chrome 复制 cookies
- [ ] **指纹深度伪装**：playwright-stealth 集成
- [ ] **Web UI**：FastAPI + 简单前端，类截图那种"环境列表"
- [ ] **detach 模式**：Python 进程退出后 Chrome 继续跑
- [ ] **profile 同步操作**：RPA Plus 那种"同步启动 / 同步关闭"

## License

MIT
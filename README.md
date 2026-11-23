# BTC 15m 缠论买卖点 → 手机推送

盯 Hyperliquid 上 BTC 永续的 15 分钟级别，一旦缠论判定出新的买卖点，就往 iPhone 推一条 Bark 通知。不用开着电脑也能跑（见「云端运行」）。

## 它是怎么工作的

```
拉 15m K线 → 丢掉未收盘的那根 → 缠论递归划分（分型→笔→线段→中枢→买卖点）
   → 只保留已确认结构 → 与上次推送记录比对 → 只有新的才推 → 写入 state.json
```

同一时间点的多个信号会合并成一条；未确认（`is_sure=False`）的结构不会被推送，因为那些点会随行情变化而消失。

## 分析引擎

内置 **chan.py**（https://github.com/Vespa314/chan.py），MIT 许可，代码已随包放在 `vendor/chan.py/`，共 297K，纯标准库、不需要 matplotlib/PyQt6。

- 许可证原文：`vendor/chan.py/LICENSE`（MIT 要求保留，请勿删除）
- 不需要安装任何 Python 包
- 计算的是经典缠论三类买卖点（1/1p/2/2s/3a/3b）

### 换引擎要注意

如果你之前在别的工具里看过缠论结构，数字可能对不上——不同实现对「笔」的严格程度定义不同，笔不一样，中枢和买卖点就会跟着变。本项目只保证自身内部的判定是自洽的。

## 本地运行

```bash
# 1. 从模板生成自己的配置（config.json 已被 gitignore，不会进仓库）
cp config.example.json config.json
#    然后编辑 config.json 里的 bark_key

# 2. 验通道
python chan15_push.py --test

# 3. 正常跑（第一次只建基线，不推历史信号）
python chan15_push.py
```

> **密钥纪律**：`config.json` 含 key，已写进 `.gitignore`。仓库里只提交 `config.example.json`（key 为空）。云端运行时 key 从 `BARK_KEY` Secret 注入。任何时候都不要把带 key 的 `config.json` 推上去。

常用参数：

| 参数 | 作用 |
|---|---|
| `--dry-run` | 只打印会推什么，不真发 |
| `--test` | 发一条测试通知 |
| `--reset` | 清空推送记录 |
| `--force-all` | 忽略 8 小时时间窗，历史信号全推（调试用） |

## 云端运行（GitHub Actions）

### 一次性配置

1. 在 GitHub 建一个仓库（**建议公开库**，原因见下方成本说明）
2. 把本目录所有文件推到仓库根目录（`.github/` 要一起推）
3. 仓库 → Settings → Secrets and variables → Actions → New repository secret
   - Name：`BARK_KEY`
   - Value：你的 Bark key
4. 仓库 → Actions → 左侧「BTC 15m 缠论买卖点推送」→ Run workflow（手动跑一次）
5. 看日志里有没有 `K线 1152 根` 和 `首次运行：已建立基线`，手机有「监控已启动」就到了

之后每 5 分钟自动跑一次，你什么都不用管。

### 当前部署

| 项目 | 值 |
|---|---|
| 仓库 | https://github.com/h2ry54g4tz-cpu/btc-chan-push（public） |
| 默认分支 | `main` |
| Secret 名称 | `BARK_KEY` |
| 触发方式 | `schedule`（每 5 分钟）+ `push`（推代码即验一次）+ `workflow_dispatch`（手动，可勾「只发测试通知」） |

### 🚨 网络问题：HTTPS 推不上去就用 SSH

首次部署时遇到 `fatal: unable to access ... Recv failure: Connection was reset`，原因是**本机网络对 github.com 的 HTTPS(443) 有干扰**，而 SSH 通道完全正常。

诊断方法（能返回 `Permission denied (publickey)` 就说明网络是通的，只是没配密钥）：

```bash
ssh -T git@github.com          # 端口 22
ssh -T -p 443 git@ssh.github.com   # 22 被封时的备用通道
```

解决：改用 SSH 远程地址

```bash
ssh-keygen -t ed25519 -C "你的邮箱" -f ~/.ssh/id_ed25519 -N ""
# 把 ~/.ssh/id_ed25519.pub 的内容加到 https://github.com/settings/ssh/new
git remote set-url origin git@github.com:<用户名>/<仓库名>.git
git push -u origin main
```

如果 22 端口也被封，远程地址改成 `ssh://git@ssh.github.com:443/<用户名>/<仓库名>.git`。

### 新建仓库后定时任务不会立刻生效

workflow 刚创建时，GitHub 需要一段时间才注册 cron。此期间 Actions 页面显示「This workflow has no runs yet」，`schedule` 一次都不会触发，**但也不报错**。

判断工作流本身有没有问题，看 `push` 触发的运行——只要它跑绿了，说明配置是好的，剩下的只是等 GitHub 把定时器挂上。

### ⚠️ 两个必须知道的限制

**1. 免费额度只够公开库**

GitHub 私有库每月免费 2000 分钟。5 分钟一次 = 每天 288 次 = 每月约 8640 次，每次按 1 分钟计 → **约 8640 分钟，是免费额度的 4 倍多**。

| 频率 | 每月运行次数 | 是否超免费额度 |
|---|---|---|
| 每 5 分钟 | ~8640 | 严重超出 |
| 每 15 分钟 | ~2880 | 超出 |
| 每 30 分钟 | ~1440 | 勉强够 |

**公开库的 Actions 是无限免费的**，所以想省钱就只能公开。但公开意味着 `chanlun_analyze.py`（第三方技能的分析引擎）也会被公开，这点你要自己权衡。Bark key 放在 Secrets 里不会泄露。

**2. 定时任务会被延迟，也可能被自动停用**

- GitHub 的 cron 在高峰期常延迟 5–15 分钟，不保证准点
- 仓库连续 60 天没有提交，GitHub 会自动停用 scheduled workflow，需要进 Actions 页重新启用

如果这两条你不能接受，替代方案是租一台香港/新加坡的轻量云服务器（约 ¥24 起/月），用 crontab 每 5 分钟跑，没有额度、延迟、停用的问题，代码一行都不用改。

## 配置项

配置文件：本地用 `config.json`（不入库），缺失时自动读 `config.example.json`。

| 字段 | 说明 |
|---|---|
| `bark_key` | Bark 的 key；也可用环境变量 `BARK_KEY` 覆盖（云端就走这个） |
| `bark_server` | 自建 Bark 服务端时改这里 |
| `bark_group` | 手机通知里的分组名 |
| `symbol` | 显示用的标的代号 |
| `hyperliquid_coin` | Hyperliquid 上的合约名 |
| `days` | 拉多少天 K 线，默认 12（要够形成笔和中枢） |
| `lookback_hours` | 多久以内的信号才算「新」，默认 8 |
| `push_kinds` | 推哪些类型：`bottom` 买点 / `top` 卖点 / `ratio` 结构信号 |
| `engine` | `chanpy`（默认，内置 MIT 引擎）；`chanlun` 仅当本机装了对应技能时可用 |
| `chanlun_dir` | 仅在 `engine=chanlun` 时使用，指向本机技能目录 |

## 引擎与许可

`vendor/chan.py/` 是从 https://github.com/Vespa314/chan.py 取的核心代码（分型/笔/线段/中枢/买卖点计算），**MIT 许可**，许可证原文在同目录 `LICENSE`。

- 只保留了计算所需模块，剔除了 `Plot/`（matplotlib）、`App/`（PyQt6）、`Image/`（7.4M 文档图）等，因此不需要装任何第三方包
- 同步上游更新：重新拉取上游仓库，把 `Chan.py`、`ChanConfig.py`、`Bi/`、`Seg/`、`ZS/`、`BuySellPoint/`、`KLine/`、`Combiner/`、`Common/`、`Math/`、`ChanModel/`、`DataAPI/`、`LICENSE` 覆盖进来

### 一个已知的上游问题

chan.py 自带的 `DataAPI/csvAPI.py` 在解析分钟级数据时有 bug：`CTime` 对 `00:00` 会按「整天」处理并把时间戳设成当天 23:59，导致跨零点的 15m 数据被判定为时间倒流。

所以本项目**不走它的 CSV 接口**，而是在 `chan_engine.py` 里用 `CTime(..., auto=False)` 手工构造 K 线喂进去。改上游代码前请注意这一点。

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `bark_key 还没填` | 既没配 config 也没设环境变量 | 二选一填上 |
| `K线不足 xxx 根` | Hyperliquid 接口异常或标的写错 | 检查 `hyperliquid_coin` |
| 收到重复通知 | state.json 丢了（云端 cache 未命中） | 正常现象，最多重复一次 |
| 一直没通知 | 本来就没新信号 | 跑 `--dry-run --force-all` 看历史信号长什么样 |
| Actions 不触发 | 仓库 60 天无活动被停用 | Actions 页点 Enable |

---

数据来自 Hyperliquid 公共接口，仅做形态结构计算，不构成投资建议。永续合约带杠杆，注意强平风险。

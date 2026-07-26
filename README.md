# TelegramDownload

这个项目用于按网易云歌单补齐下载音乐文件。程序总是先读取或生成网易云歌单 txt，然后根据 `DownloadPlatform` 选择下载后端。

当前支持：

- `Telegram`：使用原来的 Telegram bot 下载逻辑。
- `Musicn`：使用基于开源项目 `musicn` 的自动搜索/下载逻辑。
- `Other`：兼容入口，目前也走 Musicn 后端，后续可替换成新的下载平台。

## 工程文件

- `config.ini`：主配置，包含下载平台、歌单 ID、歌单文件、已有歌曲列表和 Musicn 参数。
- `.env`：Telegram API 和运行参数。第一次运行部署脚本时自动创建。
- `.env.example`：`.env` 示例。
- `Dockerfile` / `docker-compose.yml`：Docker 运行环境。
- `deploy.sh` / `deploy.ps1`：Linux/macOS 和 Windows 启动脚本。
- `DownloadScript/telegram_downloader.py`：主程序。
- `DownloadScript/musicn_auto_downloader.mjs`：Musicn 自动下载辅助脚本。
- `DownloadScript/requirements.txt`：Python 依赖。
- `DownloadScript/package.json`：Node/Musicn 依赖。
- `halp_path_list_1779699672.txt`：已有歌曲路径列表。

## config.ini

示例：

```ini
DownloadPlatform=Musicn
ResetAccount=False
PlaylistId=17961590701
PlaylistFile=2026-07-24_一部只有金属乐和乡村布鲁斯的听歌机器.txt
ExistingListFile=halp_path_list_1779699672.txt
MusicnService=migu
MusicnServices=migu,wangyi,kuwo,kugou
MusicnSearchSize=10
```

参数说明：

- `DownloadPlatform=Telegram`：使用 Telegram 下载。
- `DownloadPlatform=Musicn`：使用 Musicn 后端下载。
- `DownloadPlatform=Other`：当前等同于 Musicn，后续可改为其它平台。
- `ResetAccount=True`：仅对 Telegram 有意义，会删除本地 Telegram session 和冷却标记，重新写入 `.env`。
- `PlaylistId`：网易云音乐歌单 ID。
- `PlaylistFile`：歌单 txt。文件不存在时，程序会先用 `PlaylistId` 自动生成。
- `ExistingListFile`：已有歌曲路径列表，匹配到歌手和歌名后跳过。
- `MusicnService`：旧配置，保留兼容；如果没有 `MusicnServices`，会使用这个单服务。
- `MusicnServices`：Musicn 搜索服务列表，按顺序遍历。支持 `migu`、`wangyi`、`kuwo`、`kugou`，也可以写 `auto` 或 `all`。
- `MusicnSearchSize`：每首歌搜索候选数量，默认 10。

## .env

Telegram 后端需要：

```env
TG_API_ID=123456
TG_API_HASH=0123456789abcdef0123456789abcdef
TG_PHONE=+819012345678
TG_BOT_USERNAME=SQMP3
```

通用运行参数：

```env
SESSION_TTL_HOURS=72
MIN_DELAY=60
MAX_DELAY=150
RESPONSE_TIMEOUT=120
LONG_REST_EVERY_MINUTES=120
LONG_REST_MIN_MINUTES=30
LONG_REST_MAX_MINUTES=90
STOP_ON_SEND_BLOCKED=1
SEND_BLOCKED_COOLDOWN_HOURS=6
SUCCESS_CONFIRM_EVERY=500
```

## 运行方式

第一次或依赖变更后，Docker 会重新 build。

Linux/macOS：

```bash
chmod +x ./deploy.sh
./deploy.sh dry-run
./deploy.sh run
```

常用命令：

```bash
./deploy.sh dry-run        # 只生成待下载列表，不登录、不下载
./deploy.sh run            # 开始或继续下载
./deploy.sh restart        # 停止旧容器并重新开始
./deploy.sh stop           # 停止任务
./deploy.sh status         # 查看容器状态
./deploy.sh reset-account  # 只重置 Telegram 账号配置，不开始下载
```

Windows PowerShell：

```powershell
.\deploy.ps1 -DryRun
.\deploy.ps1
.\deploy.ps1 -ResetAccount
```

## 切换到 Musicn 下载

修改 `config.ini`：

```ini
DownloadPlatform=Musicn
MusicnService=migu
MusicnServices=migu,wangyi,kuwo,kugou
MusicnSearchSize=10
```

然后先检查待下载列表：

```bash
./deploy.sh dry-run
```

确认后开始下载：

```bash
./deploy.sh restart
```

如果想调整遍历顺序，比如优先试酷我和酷狗，可以改成：

```ini
MusicnServices=kuwo,kugou,migu,wangyi
```

## 下载流程

1. 读取 `config.ini`。
2. 检查 `PlaylistFile` 是否存在；不存在就用 `PlaylistId` 从网易云生成。
3. 读取歌单、`ExistingListFile` 和当前 `TMDownload/`。
4. 只下载缺少的歌曲。
5. 每首歌请求前再次扫描 `TMDownload/`，防止运行中重复下载。
6. Musicn 后端会按 `MusicnServices` 逐个服务尝试；全部服务都失败后，才算这首歌请求失败。
7. 任何歌曲最终请求失败立即停止，并把失败歌曲写入 `DownloadScript/FailedDownload.txt`。
8. 下次启动会优先重试失败歌曲，再继续下载新歌曲。
9. 每累计请求 2 小时，随机休息 30-90 分钟。
10. 每连续成功下载 500 首，必须输入 `yes` 才继续。

## 运行状态文件

- `TMDownload/`：下载成功的歌曲目录。
- `TMDownload/_incoming/`：临时下载目录。
- `TMDownload/rejected/`：下载到但不匹配或重复的文件。
- `DownloadScript/NewDownload.txt`：当前还需要下载的歌曲。
- `DownloadScript/FailedDownload.txt`：失败后下次优先重试的歌曲。
- `DownloadScript/SendBlockedUntil.txt`：Telegram 拒绝发送后的本地冷却标记。
- `download_summary.log`：下载日志。

## Telegram 账号被限制

如果看到：

```text
You're banned from sending messages in supergroups/channels
```

说明当前 Telegram 账号不能在目标 bot 或聊天中发消息。可以先切到 `DownloadPlatform=Musicn` 继续补齐，或者等 Telegram 权限恢复后再用：

```bash
./deploy.sh restart --force
```

# HSBC Credit Card Points Tracker

自动抓取 HSBC 网页版信用卡交易积分数据，同步到 Google Sheets。

## 功能

- 使用 Playwright 浏览器自动化登录 HSBC 网银
- 抓取每笔信用卡交易对应的积分/RewardCash
- 自动去重，同步到 Google Sheets
- 支持 Mac LaunchAgent 定时运行（每天两次）
- 保存截图和日志便于调试

## 快速开始

### 1. 安装

```bash
chmod +x setup.sh
./setup.sh
```

### 2. 配置 Google Sheets API

1. 去 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建新项目或选择已有项目
3. 启用 **Google Sheets API** 和 **Google Drive API**
4. 创建 **Service Account**:
   - IAM & Admin → Service Accounts → Create
   - 下载 JSON 密钥文件
   - 放到 `credentials/service_account.json`
5. 创建一个 Google Sheet，把 Service Account 的邮箱加为编辑者
6. 复制 Sheet 的 ID（URL 中 `/d/` 和 `/edit` 之间的部分）

### 3. 配置 .env

```bash
cp .env.example .env
nano .env
```

填入：
- `HSBC_USERNAME` - HSBC 网银用户名
- `HSBC_PASSWORD` - HSBC 网银密码
- `HSBC_BASE_URL` - 你的 HSBC 地区 URL
- `GOOGLE_SHEETS_ID` - Google Sheet ID

### 4. 首次运行（调试模式）

```bash
source .venv/bin/activate

# 非无头模式，可以看到浏览器操作
HEADLESS=false python -m src.main --dry-run
```

首次运行时建议用 `HEADLESS=false` 和 `--dry-run`，这样可以：
- 看到浏览器实际操作
- 检查登录是否成功
- 确认积分页面是否正确加载
- 不会写入 Google Sheet

### 5. 正式运行

```bash
# 单次运行（抓取 + 同步到 Google Sheet）
python -m src.main

# 或使用内置调度器
python -m src.main --schedule
```

### 6. 设置 Mac 定时任务

编辑 `com.hsbc.points-tracker.plist`，把 `/path/to/hsbcelite` 改成实际路径，然后：

```bash
cp com.hsbc.points-tracker.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.hsbc.points-tracker.plist
```

默认每天 9:00 和 21:00 运行。

查看状态：
```bash
launchctl list | grep hsbc
```

停止：
```bash
launchctl unload ~/Library/LaunchAgents/com.hsbc.points-tracker.plist
```

## 调试

- 截图保存在 `screenshots/` 目录
- 日志保存在 `hsbc_points.log`
- 如果抓取失败，检查 `screenshots/page_debug.html` 查看页面结构

### 常见问题

**登录失败？**
- 用 `HEADLESS=false` 运行查看实际页面
- 检查 `screenshots/` 中的截图
- 可能需要更新 `src/scraper.py` 中的 CSS 选择器

**找不到积分数据？**
- HSBC 不同地区页面结构不同
- 检查 `screenshots/page_debug.html`
- 更新 `scrape_transactions_with_points()` 中的选择器

**Google Sheets 写入失败？**
- 确认 Service Account 邮箱已添加到 Sheet 的共享列表
- 确认 `credentials/service_account.json` 文件存在

## 项目结构

```
hsbcelite/
├── src/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py          # 配置管理
│   ├── scraper.py         # HSBC 网页抓取
│   ├── sheets.py          # Google Sheets 同步
│   └── main.py            # 入口和调度
├── credentials/           # Google 凭证（不提交到 git）
├── screenshots/           # 调试截图（不提交到 git）
├── .env.example           # 环境变量模板
├── .gitignore
├── requirements.txt
├── setup.sh               # 安装脚本
├── com.hsbc.points-tracker.plist  # Mac 定时任务配置
└── README.md
```

## 安全提醒

- **不要** 把 `.env` 或 `credentials/` 提交到 git
- 建议使用专门的 HSBC 子账户（如果支持）
- 定期检查 HSBC 登录记录确认没有异常

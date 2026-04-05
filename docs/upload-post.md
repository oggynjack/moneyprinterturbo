# Social Upload Automation / 社交平台自动发布

[English](#english) | [中文](#中文)

## English

### Overview

0Code AutoGen supports automated publishing after video generation:

- Upload-Post (TikTok / Instagram)
- Native YouTube upload (OAuth login + Shorts/Video mode)

### What is Upload-Post?

[Upload-Post](https://upload-post.com) is an API service that allows you to upload videos to multiple social media platforms with a single API call.

- **Website:** https://upload-post.com
- **API Docs:** https://docs.upload-post.com
- **Free tier available**

### Setup

1. Create an account at [upload-post.com](https://upload-post.com)
2. Connect your TikTok and/or Instagram accounts in the dashboard
3. Get your API key from the dashboard
4. Add the following to your `config.toml`:

```toml
upload_post_enabled = true
upload_post_api_key = "your-api-key"
upload_post_username = "your-username"
upload_post_platforms = ["tiktok", "instagram"]
upload_post_auto_upload = true
```

### YouTube OAuth Upload

1. Open Google Cloud Console and create OAuth Client credentials (Desktop app).
2. Choose one setup mode:
	- File mode: download `client_secret.json`
	- Fileless mode: copy OAuth `client_id` and `client_secret`
3. Add YouTube settings to your `config.toml`:

```toml
youtube_upload_enabled = true
youtube_auto_upload = true
youtube_publish_mode = "auto" # auto | shorts | video
youtube_privacy_status = "private"
youtube_category_id = "22"
youtube_tags = ["0CodeAutoGen", "AIVideo"]
# Option A: file mode
youtube_client_secrets_file = "./credentials/client_secret.json"
# Option B: fileless mode
youtube_client_id = "your-google-oauth-client-id"
youtube_client_secret = "your-google-oauth-client-secret"
youtube_token_file = "storage/oauth/youtube_token.json"
```

4. In WebUI, open `Social Upload Automation` and click `Authorize YouTube Account`.
5. A Google login/consent page will open; authorize the target channel account.

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `upload_post_enabled` | bool | `false` | Enable/disable Upload-Post integration |
| `upload_post_api_key` | string | `""` | Your Upload-Post API key |
| `upload_post_username` | string | `""` | Your Upload-Post username |
| `upload_post_platforms` | array | `["tiktok", "instagram"]` | Platforms to cross-post to |
| `upload_post_auto_upload` | bool | `false` | Automatically cross-post after video generation |
| `youtube_upload_enabled` | bool | `false` | Enable/disable YouTube uploader |
| `youtube_auto_upload` | bool | `false` | Upload to YouTube after generation |
| `youtube_publish_mode` | string | `"auto"` | `auto`, `shorts`, or `video` |
| `youtube_privacy_status` | string | `"private"` | `private`, `unlisted`, or `public` |
| `youtube_category_id` | string | `"22"` | YouTube category id |
| `youtube_tags` | array | `["0CodeAutoGen", "AIVideo"]` | Tags for uploaded videos |
| `youtube_client_secrets_file` | string | `""` | Optional path to Google OAuth client secret file |
| `youtube_client_id` | string | `""` | OAuth client id for fileless mode |
| `youtube_client_secret` | string | `""` | OAuth client secret for fileless mode |
| `youtube_token_file` | string | `"storage/oauth/youtube_token.json"` | Path to OAuth token file |

### Usage

When `upload_post_auto_upload` is set to `true`, videos will be automatically cross-posted to the configured platforms after generation.

When `youtube_auto_upload` is enabled (or passed in request params), generated videos are uploaded to YouTube automatically.

Result fields in task response:

- `cross_post_results`: Upload-Post results
- `youtube_upload_results`: YouTube upload results

---

## 中文

### 概述

0Code AutoGen 支持视频生成后的自动发布：

- Upload-Post（TikTok / Instagram）
- 原生 YouTube 上传（OAuth 登录 + Shorts/普通视频模式）

### 什么是 Upload-Post？

[Upload-Post](https://upload-post.com) 是一个 API 服务，允许您通过单个 API 调用将视频上传到多个社交媒体平台。

- **网站：** https://upload-post.com
- **API 文档：** https://docs.upload-post.com
- **提供免费套餐**

### 设置

1. 在 [upload-post.com](https://upload-post.com) 创建账户
2. 在控制面板中连接您的 TikTok 和/或 Instagram 账户
3. 从控制面板获取您的 API 密钥
4. 将以下内容添加到您的 `config.toml`：

```toml
upload_post_enabled = true
upload_post_api_key = "your-api-key"
upload_post_username = "your-username"
upload_post_platforms = ["tiktok", "instagram"]
upload_post_auto_upload = true
```

### YouTube OAuth 上传

1. 在 Google Cloud Console 创建 OAuth Client（Desktop app）。
2. 可选两种配置方式：
	- 文件模式：下载 `client_secret.json`
	- 无文件模式：直接复制 OAuth `client_id` 和 `client_secret`
3. 在 `config.toml` 增加配置：

```toml
youtube_upload_enabled = true
youtube_auto_upload = true
youtube_publish_mode = "auto" # auto | shorts | video
youtube_privacy_status = "private"
youtube_category_id = "22"
youtube_tags = ["0CodeAutoGen", "AIVideo"]
# 方式 A：文件模式
youtube_client_secrets_file = "./credentials/client_secret.json"
# 方式 B：无文件模式
youtube_client_id = "your-google-oauth-client-id"
youtube_client_secret = "your-google-oauth-client-secret"
youtube_token_file = "storage/oauth/youtube_token.json"
```

4. 在 WebUI 打开 `Social Upload Automation`，点击 `Authorize YouTube Account`。
5. 系统会弹出 Google 登录授权页面，选择要发布视频的频道账号完成授权。

### 配置选项

| 选项 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `upload_post_enabled` | bool | `false` | 启用/禁用 Upload-Post 集成 |
| `upload_post_api_key` | string | `""` | 您的 Upload-Post API 密钥 |
| `upload_post_username` | string | `""` | 您的 Upload-Post 用户名 |
| `upload_post_platforms` | array | `["tiktok", "instagram"]` | 要发布的平台 |
| `upload_post_auto_upload` | bool | `false` | 视频生成后自动发布 |
| `youtube_upload_enabled` | bool | `false` | 启用/禁用 YouTube 上传 |
| `youtube_auto_upload` | bool | `false` | 生成后自动上传到 YouTube |
| `youtube_publish_mode` | string | `"auto"` | `auto`、`shorts`、`video` |
| `youtube_privacy_status` | string | `"private"` | `private`、`unlisted`、`public` |
| `youtube_category_id` | string | `"22"` | YouTube 分类 ID |
| `youtube_tags` | array | `["0CodeAutoGen", "AIVideo"]` | 上传标签 |
| `youtube_client_secrets_file` | string | `""` | 可选，Google OAuth 客户端文件路径 |
| `youtube_client_id` | string | `""` | 无文件模式的 OAuth client id |
| `youtube_client_secret` | string | `""` | 无文件模式的 OAuth client secret |
| `youtube_token_file` | string | `"storage/oauth/youtube_token.json"` | OAuth Token 保存路径 |

### 使用方法

当 `upload_post_auto_upload` 设置为 `true` 时，视频在生成后将自动发布到配置的平台。

当启用 `youtube_auto_upload`（或在请求参数中开启）时，生成完成后会自动上传到 YouTube。

任务返回结果字段：

- `cross_post_results`：Upload-Post 发布结果
- `youtube_upload_results`：YouTube 上传结果

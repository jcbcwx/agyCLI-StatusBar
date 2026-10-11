# agyCLI-StatusBar
AntigravityCLI ( agy ) - Status Bar - Small Script

<img width="1886" height="161" alt="Screenshot From 2026-10-11 07-03-13" src="https://github.com/user-attachments/assets/81e4f5f3-e346-4c17-9e6c-b26fb0af91bd" />


  ──────
  
### To configure and use status.py as the custom status bar in Antigravity CLI (agy), follow these steps:

  ──────
  
  ### 1. Register the Script in agy

  Add the statusLine configuration to your CLI settings file at ~/.gemini/antigravity-cli/settings.json:

    {
      "statusLine": {
        "type": "command",
        "command": "python3 ~/.gemini/antigravity-cli/status.py",
        "enabled": true
      }
    }

  Ensure the script has executable permissions if invoked directly:

    chmod +x  status.py


  Finish！


  ──────
  
  ### 2. Environment Variables Configuration（Optional）

  status.py:52-65 supports optional environment variables to override default file paths and cache timings:

   Variable                           | Default Value                                      | Description
  ------------------------------------|---------------------------------------------|--------------------------------------------------------------
   AGY_STATUS_STATE                   | ~/.gemini/antigravity-cli/status-state.json | Path where SessionMetricsTracker stores session timing & TPS metrics.
   AGY_QUOTA_CACHE                    | ~/.gemini/antigravity-cli/quota-cache.json  | Path where quota snapshot responses from the language server are cached.
   AGY_STATUS_ERROR_LOG               | ~/.gemini/antigravity-cli/status-error.log  | Log destination for unhandled errors.
   AGY_QUOTA_REFRESH_INTERVAL_SECONDS | 30                                          | Minimum interval (in seconds) between quota refresh requests.
   AGY_QUOTA_MAX_AGE_SECONDS          | 900                                         | Max age (in seconds) before cached quota data is marked stale.

  ──────
  
  ### 3. Customizing Appearance & Behavior in status.py（Optional）

  You can tweak the display directly in the script:

  - Tips & Rotation:
    - Update TIPS to customize the rotating tip messages.
    - Adjust TIP_MIN_SECONDS (default 3.0) for tip switch frequency.
  - Status Bar Layout:
    - Modify _render_internal (lines 794–803) to reorder or hide status elements (directory, token I/O, context utilization, session duration, TPS, model, quota).
  - ANSI Colors & Styling:
    - Update color definitions around lines 20–33 or color threshold functions like context_color and quota_color.


<br>
##############################################################################################



### 在 Antigravity CLI (agy) 中配置并启用 status.py 作为自定义状态栏的步骤如下：

  ──────
  
  ### 1. 在 agy 中注册脚本
  编辑 CLI 配置文件 ~/.gemini/antigravity-cli/settings.json，添加 statusLine 配置项：

    {
      "statusLine": {
        "type": "command",
        "command": "python3 ~/.gemini/antigravity-cli/status.py",
        "enabled": true
      }
    }
  赋予脚本可执行权限：

    chmod +x status.py

  完成！

  
  ──────
  
  ### 2. 环境变量配置（可选）

  status.py:52-65 支持通过环境变量自定义文件路径与缓存刷新时间：

   环境变量                           | 默认值                                      | 说明
  ------------------------------------|---------------------------------------------|--------------------------------------------------------------
   AGY_STATUS_STATE                   | ~/.gemini/antigravity-cli/status-state.json | SessionMetricsTracker 记录会话耗时、TPS 等指标的状态文件路径
   AGY_QUOTA_CACHE                    | ~/.gemini/antigravity-cli/quota-cache.json  | 缓存 Language Server 配额快照的文件路径。
   AGY_STATUS_ERROR_LOG               | ~/.gemini/antigravity-cli/status-error.log  | 未捕获异常的错误日志输出路径。
   AGY_QUOTA_REFRESH_INTERVAL_SECONDS | 30                                          | 向本地服务刷新配额的最小时间间隔（秒）。
   AGY_QUOTA_MAX_AGE_SECONDS          | 900                                         | 配额缓存的最大有效时间（秒，超时视为过期）。
   
  ──────
  
  ### 3. 在 status.py 中自定义显示与行为（可选）

  你可以直接修改脚本源码来调整状态栏外观：

  - 提示语与轮换频率：
      - 修改 TIPS 列表以自定义滚动显示的提示内容。
      - 调整 TIP_MIN_SECONDS（默认 3.0 秒）修改提示轮换间隔。
  - 状态栏元素排版：
      - 在 _render_internal（第 794–803 行）调整或隐藏字段（包括当前目录、输入/输出 Token、上下文占用、会话耗时、TPS、当前模型、配额倒计时）。
  - ANSI 配色与样式：
      - 在第 20–33 行 修改颜色变量，或修改 context_color 和 quota_color 的阈值配色逻辑。

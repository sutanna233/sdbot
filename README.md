# SD Artist Combo Tester

基于 Stable Diffusion WebUI API 的艺术家组合批量测试工具。

## 功能

- 5 种采样模式（combo / single / pair / sequential / weighted）
- 组合去重，跨运行不重复
- 相似艺术家自动检测
- 命令行界面，支持子命令
- 生成记录持久化（JSON）

## 环境要求

- Python 3.10+
- Stable Diffusion WebUI 运行中（默认端口 7860）

## 安装

```bash
pip install requests pyyaml Pillow
```

## 快速开始

```bash
# 进入目录
cd sd_artist_tester

# 直接运行（combo 模式，生成 10 张）
python generate_artists.py run

# 指定模式和数量
python generate_artists.py run --mode single --num 50
```

## CLI 命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `run` | 生成图片 | `run --mode combo --num 20` |
| `status` | 查看当前状态 | `status` |
| `history` | 查看生成历史 | `history --last 10` |
| `artists` | 艺术家列表 | `artists --search "sakura"` |
| `config` | 配置管理 | `config set mode single` |
| `clear` | 清理数据 | `clear history` |

### run — 生成图片

```bash
python generate_artists.py run [选项]

选项:
  --mode    combo|single|pair|sequential|weighted   采样模式
  --num N   生成数量
  --resume  跳过已生成的组合（去重）
  --no-dedup  本次运行关闭去重
```

**模式说明：**

| 模式 | 说明 |
|------|------|
| `combo` | 每张图随机选 3-8 个艺术家组合（默认） |
| `single` | 每个艺术家单独生成 |
| `pair` | 两两配对 |
| `sequential` | 按列表顺序分块（默认 5 个一组） |
| `weighted` | 按权重采样（冷门优先） |

### status — 查看状态

```bash
python generate_artists.py status
# 输出: API、模式、去重、艺术家数、已生成组合数、历史运行记录
```

### history — 查看历史

```bash
python generate_artists.py history
python generate_artists.py history --last 20
python generate_artists.py history --search "sakura"
```

### artists — 艺术家管理

```bash
python generate_artists.py artists list          # 列出所有
python generate_artists.py artists list --search "sak"  # 搜索
python generate_artists.py artists count         # 数量
python generate_artists.py artists gen           # 已生成过的
```

### config — 配置管理

```bash
python generate_artists.py config show                    # 显示全部配置
python generate_artists.py config get mode                 # 获取单个值
python generate_artists.py config set mode single          # 设置
python generate_artists.py config set generation.steps 30  # 嵌套键
python generate_artists.py config set generation.seed 1234 # 设为数字
```

### clear — 清理

```bash
python generate_artists.py clear history  # 清除去重历史
python generate_artists.py clear outputs  # 清除所有生成的图片
```

## 配置文件

复制示例配置后再填写本地密钥：

```bash
cp config.example.yaml config.yaml
```

`config.yaml` 会被 `.gitignore` 排除，不要提交真实 API key、Telegram token 或本地会话数据。

`config.yaml` 关键配置项：

```yaml
mode: "combo"          # combo | single | pair | sequential | weighted

mode_config:
  combo:
    min_artists: 3
    max_artists: 8

sd_api:
  base_url: "http://127.0.0.1:7860"
  auth: null           # 或 "user:password"

dedup:
  enabled: true
  similarity_filter: "strict"   # strict / off
  allow_resample: false
  history_file: "history.json"

generation:
  width: 512
  height: 512
  steps: 20
  cfg_scale: 7
  seed: -1
  sampler: "Euler a"

testing:
  num_images: 10
  retry: 2
  continue_on_error: true

output:
  base_dir: "./outputs"
```

## 文件结构

```
sd_artist_tester/
├── generate_artists.py   # 主程序
├── config.example.yaml   # 配置模板
├── config.yaml           # 本地配置（不提交）
├── artists.txt           # 艺术家列表（每行一个）
├── history.json          # 生成历史（自动生成）
├── run.bat               # Windows 启动
├── run.sh                # Linux 启动
└── outputs/              # 生成结果
    └── {timestamp}_{mode}_test/
        ├── combo_001_*.png
        ├── combo_001_*.txt
        └── generation_log.json
```

## 艺术家列表格式

`artists.txt` 每行一个艺术家 ID：

```
kotomaru_(kotokoto_kottan)
kawamochi_(kawauti919)
yukitake_(bullfalk)
```

列表越大，可用组合越多。当前 7405 个艺术家。

# docview

在终端里只读预览 **飞书云文档** 和本地 **Markdown / HTML** 文件，文件改动后自动刷新。

适合这样用：终端左右分屏，一边让 AI agent（Claude Code 等）改文档，一边用 docview 看效果。
在 [herdr](https://herdr.dev)、tmux 或任何普通终端里都能运行，也可以通过 SSH 在手机上看。

```
 项目周报 · 飞书 · rev 42 · 更新于 10:05:21
                                   本周进展
  • 完成登录模块重构
  • 待办：
     1 补充接口文档
     2 联调支付流程
                                   任务列表
 ┏━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┓
 ┃ 模块     ┃ 说明                 ┃ 状态     ┃ 下一步                   ┃
 ┡━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━┩
 │ 登录     │ 改用新的鉴权服务     │ 已完成   │ 1.灰度发布               │
 │          │                      │          │ 2.观察错误率             │
 └──────────┴──────────────────────┴──────────┴──────────────────────────┘
 q 退出  o 打开其他  r 刷新  j 下  k 上  space 翻页  g 顶部  G 底部
```

## 功能

- **飞书文档**：支持 docx 和知识库（wiki）链接。表格（包括合并单元格）、列表、链接都能显示；图片、附件、文档引用显示为 `[附件: 名称]`、`[文档: 标题]` 这样的占位符。
- **本地文件**：`.md` / `.markdown` / `.html` / `.htm`。HTML 会去掉 `<script>`、`<style>` 后转成 Markdown 显示。
- **自动刷新**：本地文件保存后约 1 秒内刷新；飞书文档每 20 秒检查一次版本号，有变化才重新渲染。
- **开始页**：不带参数运行时显示文件树，只列出文件夹和支持的文件，也可以直接输入路径或飞书链接。
- **只读**：docview 不会修改任何文档。

## 安装

依赖：

- [uv](https://docs.astral.sh/uv/)：自动准备 Python 依赖（Textual、markdownify、BeautifulSoup），不需要手动建虚拟环境。
- [lark-cli](https://github.com/larksuite/cli)：只有看飞书文档时需要。安装后先登录：
  ```bash
  npm install -g @larksuite/cli
  lark-cli auth login
  ```

安装 docview：

```bash
git clone https://github.com/santaealan/docview.git
ln -sf "$PWD/docview/bin/docview" ~/.local/bin/docview   # 确保 ~/.local/bin 在 PATH 里
```

第一次运行时 uv 会下载依赖，需要等几秒，之后启动就很快了。

## 用法

```bash
docview                                   # 开始页，从当前目录选择文件
docview ~/notes                           # 开始页，从指定目录选择文件
docview notes.md                          # 打开本地 Markdown
docview page.html                         # 打开本地 HTML
docview https://xxx.feishu.cn/docx/XXXX   # 打开飞书文档
docview https://xxx.feishu.cn/wiki/XXXX   # 打开飞书知识库文档
docview --refresh                         # 让正在运行的 docview 立即重新加载
```

docview 在当前终端里直接运行，按 `q` 退出。

### 按键

开始页：

| 按键 | 作用 |
|---|---|
| ↑ / ↓、回车 | 在文件树里移动；回车展开文件夹或打开文件，也可以用鼠标点 |
| `u` | 回到上级目录 |
| `Shift+Tab` / `Tab` | 在输入框和文件树之间切换 |
| 输入框里回车 | 打开输入的文件、目录或飞书链接；相对路径以文件树当前目录为准 |
| `q` | 退出 |

文档页：

| 按键 | 作用 |
|---|---|
| `j` / `k`、↓ / ↑、鼠标滚轮 | 滚动 |
| `space` / `b` | 向下 / 向上翻一页 |
| `g` / `G` | 跳到顶部 / 底部 |
| `r` | 立即重新加载 |
| `o` | 回到开始页 |
| `q` | 退出 |

## 和 AI agent 配合

1. 把终端分成左右两个窗格。
2. 一边运行 `docview <文档链接或文件>`。
3. 另一边让 agent 修改文档。飞书文档可以让 agent 用 `lark-cli docs +update` 做局部修改。
4. agent 改完后运行 `docview --refresh`，预览会立即更新。不运行的话，本地文件约 1 秒、飞书文档最多约 20 秒也会自动刷新。

可以把下面这句加到 agent 的说明里（比如 `CLAUDE.md`）：

> 修改飞书文档或预览中的本地文档后，运行 `docview --refresh`。

`--refresh` 会刷新同一个 herdr workspace 里所有运行中的 docview；不在 herdr 里时，会刷新本机所有不在 herdr 里运行的 docview。

## 通过 SSH 使用的流量

飞书文档由运行 docview 的那台电脑拉取，不经过 SSH 客户端。SSH 上只传屏幕变化的部分：

- 空闲时很少：实测 docview 空闲时每分钟只输出约 3 KB 的屏幕数据。
- 主要流量来自滚动：鼠标滚轮或触摸滑动每滚一下都会重画可见区域。想省流量就多用 `space` / `b` 整页翻。

## 限制

- 终端里显示不了图片，画板、嵌入的多维表格等也只显示为占位符。
- Markdown 只做阅读用的近似渲染，不保证和飞书网页版的样式一致。
- 飞书文档变化最多要 20 秒才能被发现（可以用 `--refresh` 立即刷新）。

## 工作原理

```
bin/docview    入口脚本：解析参数，把要打开的目标写入状态文件，用 uv 准备好依赖环境后，直接用其中的 Python 启动 docview.py（uv 不常驻）
docview.py     Textual 界面：开始页、渲染、定时检查更新
```

- 每个运行中的 docview 在 `~/.local/state/docview/` 下有一个状态文件，里面写着当前打开的目标。`docview --refresh` 只是更新这些文件的修改时间，docview 发现后就会重新加载。docview 退出时会删除自己的状态文件。
- 飞书文档通过 `lark-cli docs +fetch --doc-format markdown` 获取。返回的 Markdown 里夹带的 HTML 表格由 docview 自己转成终端表格，其余部分交给 Rich 渲染。

## 许可证

[MIT](LICENSE)

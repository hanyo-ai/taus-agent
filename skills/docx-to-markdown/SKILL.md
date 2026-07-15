---
name: docx-to-markdown
description: 将 .docx 文件转换为 Markdown——使用微软 MarkItDown 库，支持 pickle 缓存避免重复解析，返回 Markdown 纯文本
---

# Docx to Markdown

使用微软 `markitdown` 库将 `.docx` 文件转为 Markdown 文本，附带 pickle 缓存层，72 小时内同一文件不重复解析。

## 依赖

```bash
source .venv/bin/activate
```

```bash
uv add markitdown[docx]>=0.1.1
```

> `markitdown` 底层用 `mammoth` 处理 `.docx`，输出为标准 Markdown。

## 核心代码

### 缓存层

基于 `pickle` 序列化，以文件路径 + 参数做 MD5 生成缓存 key，默认 72 小时过期。

```python
import os
import pickle
import hashlib
import time
from pathlib import Path
from typing import Any, Optional


class Cache:
    """pickle 文件缓存，支持过期时间"""

    def __init__(self, cache_dir: str = "./md_cache", expiry_hours: int = 72):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.expiry_seconds = expiry_hours * 3600

    def _get_cache_key(self, filename: str, **kwargs) -> str:
        content = f"{filename}:{str(kwargs)}"
        return hashlib.md5(content.encode()).hexdigest()

    def get(self, filename: str, **kwargs) -> Optional[Any]:
        cache_key = self._get_cache_key(filename, **kwargs)
        cache_file = self.cache_dir / f"{cache_key}.pickle"

        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "rb") as f:
                data = pickle.load(f)
                if time.time() - data["timestamp"] > self.expiry_seconds:
                    os.remove(cache_file)
                    return None
                return data["content"]
        except Exception:
            return None

    def set(self, filename: str, content: Any, **kwargs) -> None:
        cache_key = self._get_cache_key(filename, **kwargs)
        cache_file = self.cache_dir / f"{cache_key}.pickle"

        with open(cache_file, "wb") as f:
            pickle.dump({"timestamp": time.time(), "content": content}, f)

    def cleanup(self) -> None:
        current_time = time.time()
        for cache_file in self.cache_dir.glob("*.pickle"):
            try:
                with open(cache_file, "rb") as f:
                    data = pickle.load(f)
                if current_time - data["timestamp"] > self.expiry_seconds:
                    os.remove(cache_file)
            except Exception:
                os.remove(cache_file)
```

### 转换函数

```python
from markitdown import MarkItDown

_md = MarkItDown(enable_plugins=False)
_cache = Cache()


def docx_to_markdown(filepath: str, use_cache: bool = True) -> dict:
    """
    将 .docx 文件转换为 Markdown。

    Args:
        filepath: .docx 文件的绝对路径
        use_cache: 是否使用缓存（默认 True，72h 过期）

    Returns:
        {"markdown": "..."}
    """
    if use_cache:
        cached = _cache.get(filepath, return_md=True)
        if cached:
            return {"markdown": cached}

    # 调用 MarkItDown 解析
    markdown_content = _md.convert(filepath).text_content

    if use_cache:
        _cache.set(filepath, markdown_content, return_md=True)

    return {"markdown": markdown_content}
```

### 大文档异步处理（可选）

对于大型文档，避免阻塞事件循环：

```python
import concurrent.futures


def convert_async(filepath: str) -> dict:
    """在线程池中执行转换，适用于 async 环境（FastAPI / asyncio）"""
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(docx_to_markdown, filepath)
        return future.result()
```

## 完整示例

```python
from pathlib import Path

# 1. 转换单个文件
result = docx_to_markdown("/path/to/document.docx")
print(result["markdown"])

# 2. 批量转换
doc_dir = Path("./documents")
for docx_file in doc_dir.glob("*.docx"):
    md = docx_to_markdown(str(docx_file))
    md_path = docx_file.with_suffix(".md")
    md_path.write_text(md["markdown"])
    print(f"✅ {docx_file.name} → {md_path.name}")

# 3. 不使用缓存（强制重新解析）
result = docx_to_markdown("/path/to/document.docx", use_cache=False)
```

## 与 FastAPI 集成

```python
import hashlib
import os
from fastapi import APIRouter, UploadFile, File

router = APIRouter()
UPLOAD_DIR = "./tmp"


@router.post("/upload")
async def upload_docx(file: UploadFile = File(...)):
    content = await file.read()
    file_hash = hashlib.md5(content).hexdigest()
    ext = os.path.splitext(file.filename)[1]
    filepath = os.path.join(UPLOAD_DIR, f"{file_hash}{ext}")

    # 保存文件（如果已存在则跳过）
    if not os.path.exists(filepath):
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(content)

    # 转 Markdown（自动走缓存）
    result = docx_to_markdown(filepath)

    return {
        "original_filename": file.filename,
        "saved_filename": f"{file_hash}{ext}",
        "markdown": result["markdown"],
    }
```

## Markdown 后处理

```python
import re


def extract_sections(md_text: str) -> list[dict]:
    """
    解析 Markdown 标题层级，构建树状结构。

    Returns:
        [
            {
                "level": 1,
                "title": "第一章",
                "content": "段落文本...",
                "children": [
                    {"level": 2, "title": "1.1 小节", "content": "...", "children": []}
                ]
            },
            ...
        ]
    """
    pattern = re.compile(r"^(#{1,6})\s+(.*)", re.MULTILINE)
    matches = list(pattern.finditer(md_text))

    flat = []
    for i, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        content = md_text[start:end].strip()
        flat.append({"level": level, "title": title, "content": content})

    # 构建树
    stack = []
    tree = []
    for sec in flat:
        node = {**sec, "children": []}
        while stack and stack[-1]["level"] >= sec["level"]:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            tree.append(node)
        stack.append(node)

    return tree
```

## 关键经验

### 1. 用文件路径而非内容做缓存 key

缓存 key 基于 `filepath + kwargs` 的 MD5，不是文件内容。这是设计选择：同一个文件路径 = 同一缓存条目。如果你的场景是「同一内容不同路径」，需要改为对文件内容做 hash。

### 2. `enable_plugins=False` 的原因

`MarkItDown` 的插件系统（如 Azure Document Intelligence）依赖网络调用和额外凭证。本地离线转换只需 `mammoth` 引擎，关闭插件避免意外报错。

### 3. 大文档用 ThreadPoolExecutor

`markitdown` 是同步阻塞的。在 async 环境（FastAPI / asyncio）中直接调用会阻塞事件循环，应提交到线程池执行。

### 4. 缓存清理

`cleanup()` 可周期性调用（如定时任务）来删除过期 pickle 文件，避免磁盘堆积。

### 5. mammoth vs python-docx

| | mammoth（markitdown 底层） | python-docx |
|---|---|---|
| 输出 | Markdown / HTML | Python 对象 |
| 用途 | **文档内容提取** | 文档创建/编辑 |
| 保真度 | 语义转换（段落→p，标题→h1） | 精确到 XML 级别 |

如果需要提取文档中的**图片、表格、注释**等结构化元素，markitdown + mammoth 是最佳选择。

## 适用场景

- 将用户上传的 `.docx` 转为 Markdown，供 LLM / Agent 处理
- 文档知识库构建：批量转换 docx → md → 向量化
- 文档内容审查 / 国标提取等下游任务的前置步骤
- 替代 pandoc，无需系统级安装，纯 Python 依赖

## 与 pandoc 的对比

| | markitdown | pandoc |
|---|---|---|
| 安装 | `uv add markitdown[docx]` | 系统级安装（apt/brew/choco） |
| 格式支持 | docx / pdf / pptx / xlsx / html / 图片等 | 几乎所有文档格式 |
| Python 集成 | 原生 | 需 subprocess 调用 |
| 轻量程度 | ~几十 MB（纯 Python） | ~数百 MB（Haskell 运行时） |
| 表格质量 | 较好 | 优秀 |

"""向量/检索子包：职责拆分为三个模块。

- ``embedding``：文本/图像向量编码（Ollama all-minilm / CLIP）。
- ``store``：LanceDB 持久化（写入、检索、删除、状态）。
- ``similarity``：检索编排（混合相似推荐、标签兜底、向量回填）。

外部调用方沿用旧路径（``app.services.embedding_service`` /
``app.services.vector_service`` / ``app.services.vector_store``），
它们是对本子包的 re-export 薄壳，保证向后兼容。
"""

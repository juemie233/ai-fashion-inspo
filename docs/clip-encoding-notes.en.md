# CLIP Encoding Notes (Vectorization Foundation of This Project)

> Related features: one-click vectorization on the admin page "Vector Management", similar recommendations on the detail page, semantic search, and the quality pre-filter.
> Background: the root cause of the lag when opening the inspiration detail page is that inspirations without image vectors get CLIP-encoded on the fly inside the request chain.

---

## 1. In One Sentence

**CLIP encoding "translates" an image into 512 numbers (a vector), letting the computer compare how similar images are in a mathematical way.**

## 2. What Is CLIP

CLIP (Contrastive Language-Image Pre-training) is a **multimodal model** open-sourced by OpenAI that understands both images and text. During training it learned one thing: **semantically similar images and text are also close to each other in vector space**.

- Feed in a "French dress street-style photo" → the output vector is **close in direction** to the vector of the text "French dress"
- This is the key that sets it apart from traditional image models (which only look at images): **the vector carries "semantics"**, not just pixel features

## 3. What "Encoding" (Embedding) Actually Does

```
Image (pixel matrix) ──CLIP model──→ [0.021, -0.134, 0.087, ... , 0.053]  ← 512 floating-point numbers
                                       ↑ This is the "vector" / "Embedding"
```

Encoding = a single **forward inference pass**: the image goes through the model's multi-layer neural network and is finally compressed into a fixed-dimension vector (in this project: **512 dimensions**, model `clip-ViT-B/32` from the sentence-transformers library).

**Why 512 numbers?** Because the model "compresses" the image into a feature description of 512 dimensions — each dimension represents some abstract feature (some may be close to "skirt hems", some close to "warm tones", some close to "portraits"). The fixed dimension lets all images be **directly compared**.

## 4. What Vectors Can Do (Actual Uses in This Project)

| Use Case | Principle | Project Location |
|---|---|---|
| **Image search / Similar recommendations** | Two image vectors with close **cosine similarity** = visually/semantically similar | `cosine_similarity` and `find_similar_hybrid` in `similarity.py` ("similar inspirations" on the detail page) |
| **Semantic search** | Text is also encoded into vectors (384 dimensions, Ollama all-minilm); text vectors are compared against image vectors, enabling searches like "white dress" | Vector retrieval + hybrid keyword ranking |
| **Quality pre-filter** | Trains a sklearn classifier on image vectors to identify junk inspirations | `quality_learner.py` |
| **Vector storage** | All vectors are stored in **LanceDB** (embedded vector database; local files under `storage/lancedb/`) | `vector/store.py` |

**Key point: similarity computation (comparison) is extremely fast** (microsecond level), but **encoding (generating vectors) is slow** (model inference, hundreds of milliseconds to several seconds per image, depending on CPU/GPU).

## 5. Why the Detail Page Lags (Root Cause of the Pain Point)

```
Normal path (vector already exists):
  Open detail → Similar recommendations → Query LanceDB for the vector → Compare (fast, millisecond level)

Lagging path (no vector):
  Open detail → Similar recommendations → No vector found → Call CLIP to encode this image on the fly (1~3 s!)
               → Only then get the vector to compare → Page freezes
```

`_get_or_build_image_vector` (`vector/similarity.py`) is exactly what does "generate on the fly if missing" — **the encoding blocks synchronously inside the request chain**, so opening inspirations without vectors lags noticeably.

**Solution** (already live): on the admin page "Vector Management" → "One-click vectorize missing inspirations", all inspirations are encoded in advance and stored in LanceDB (an asynchronous background task executed by a worker), so the detail page always takes the fast path. In the real environment, 956 of 3,154 images (30%) once lacked vectors.

## 6. A Simple Analogy

- **CLIP encoding** = writing a "semantic résumé in a unified format" (512 words) for each photo
- **Vector database (LanceDB)** = the filing cabinet that holds all the résumés
- **Similar recommendations** = take the current photo's résumé and quickly flip through the filing cabinet to find the one with "the most similar history"
- Lag = when you hit a photo without a résumé, you write one on the spot (very slow) instead of directly looking it up in the cabinet

## 7. Additional Notes

- This project uses **`clip-ViT-B/32`** (ViT-Base architecture, 32×32 patches, ~150 million parameters) with 512-dimensional output; the model is downloaded automatically on first use
- **Encoding results can be reused offline** — the encoding of the same image is deterministic, so "encode once, reuse long-term" is worthwhile (this is exactly the meaning of the vectorization task)
- Text vectors go through Ollama `all-minilm` (384 dimensions) and are stored in separate tables from image vectors (`text_vectors` / `image_vectors`)
- If encoding becomes unacceptably slow, viable optimization directions: switch to a lighter CLIP variant (e.g., ViT-S/16) or switch to GPU inference; but for a scale of 3000+ inspirations, one-time background encoding is the optimal solution

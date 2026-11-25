"""生成点击式腿部姿态人工标注页（独立 HTML，不依赖后端服务）。

读取 gold-set.json 和已存在的 annotations-simple.xlsx，生成：

    <gold_dir>/annotate.html

页面支持：
- 单张大图
- 是并拢 / 不是并拢
- 键盘 1 / 0 / 左右方向键
- 自动跳到下一张未标注
- 本地 localStorage 保存
- 导出 CSV / JSON

用法：
    python backend/scripts/leg_posture_annotate_page.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from openpyxl import load_workbook

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成点击式腿部姿态标注页")
    parser.add_argument(
        "--gold-dir",
        type=Path,
        default=Path(settings.storage_root) / "audit" / "leg-posture-gold-20261124-205716",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def _existing_labels(gold_dir: Path) -> dict[int, bool]:
    """合并 xlsx 和人工标签 JSON；JSON 优先于 xlsx。"""
    labels: dict[int, bool] = {}
    xlsx = gold_dir / "annotations-simple.xlsx"
    if xlsx.is_file():
        sheet = load_workbook(xlsx, data_only=True).active
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if row[0] is None or row[5] is None:
                continue
            labels[int(row[0])] = bool(row[5])
    labels_json = gold_dir / "human-labels-200.json"
    if labels_json.is_file():
        data = json.loads(labels_json.read_text(encoding="utf-8"))
        for row in data.get("rows", []):
            # 兼容 human_meaningful / meaningful_together
            value = row.get("human_meaningful", row.get("meaningful_together"))
            if value is not None:
                labels[int(row["gold_no"])] = bool(value)
    return labels


def _records(gold_dir: Path, labels: dict[int, bool]) -> list[dict]:
    data = json.loads((gold_dir / "gold-set.json").read_text(encoding="utf-8"))
    root = Path(settings.storage_root)
    out = []
    for record in data["records"]:
        no = int(record["gold_no"])
        media = str(record["file_path"])
        # 视频页无法直接作为 <img>，使用缩略图；普通图片用原图以保留腿部细节
        if Path(media).suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}:
            media = record.get("thumbnail_path") or media
        image_path = (root / media).as_posix()
        sheet = (no - 1) // 20 + 1
        out.append(
            {
                "no": no,
                "image": "file:///" + image_path,
                "sheet": "file:///" + (gold_dir / "contact-sheets" / f"sheet-{sheet:02d}.jpg").as_posix(),
                "sheet_name": f"sheet-{sheet:02d}.jpg",
                "initial": labels.get(no),
            }
        )
    return out


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>腿部并拢 · 点击标注</title>
<style>
  :root { color-scheme: light; --bg:#f6f4ef; --card:#fffdf8; --ink:#2b2b2b; --muted:#7b7468; --line:#e6e0d4; --green:#2f6b4f; --red:#a8423a; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; }
  header { display:flex; align-items:center; gap:16px; padding:14px 22px; border-bottom:1px solid var(--line); background:var(--card); position:sticky; top:0; z-index:2; }
  header h1 { font-size:16px; margin:0; letter-spacing:.02em; }
  header .sub { color:var(--muted); font-size:12px; }
  main { max-width:1180px; margin:0 auto; padding:18px 22px 32px; display:grid; grid-template-columns:minmax(0,1fr) 310px; gap:18px; }
  @media (max-width:900px){ main{grid-template-columns:1fr;} }
  .stage { background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden; }
  .bar { display:flex; align-items:center; gap:12px; padding:10px 14px; border-bottom:1px solid var(--line); font-size:13px; color:var(--muted); }
  .bar strong { color:var(--ink); font-size:16px; }
  .progress { flex:1; height:8px; background:#e8e2d6; border-radius:99px; overflow:hidden; }
  .progress > i { display:block; height:100%; width:0; background:var(--green); transition:width .2s; }
  .photo-wrap { display:flex; align-items:center; justify-content:center; min-height:420px; background:#ece8df; padding:10px; }
  #photo { max-width:100%; max-height:72vh; object-fit:contain; display:block; }
  .toolbar { display:flex; flex-wrap:wrap; align-items:center; gap:8px; padding:12px 14px; border-top:1px solid var(--line); }
  button { border:1px solid var(--line); background:#fff; color:var(--ink); border-radius:9px; padding:9px 14px; font:inherit; cursor:pointer; }
  button:hover { background:#f3efe7; }
  button.primary { color:#fff; background:var(--green); border-color:var(--green); }
  button.danger { color:#fff; background:var(--red); border-color:var(--red); }
  button.ghost { color:var(--muted); }
  button:disabled { opacity:.45; cursor:default; }
  .hint { color:var(--muted); font-size:12px; margin-left:auto; }
  aside { display:flex; flex-direction:column; gap:12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; }
  .card h2 { margin:0 0 8px; font-size:13px; letter-spacing:.04em; color:var(--muted); font-weight:600; }
  .stat { display:flex; justify-content:space-between; font-size:13px; padding:3px 0; }
  .stat b { font-size:15px; }
  textarea { width:100%; min-height:72px; resize:vertical; border:1px solid var(--line); border-radius:9px; padding:8px; font:inherit; }
  .pill { display:inline-block; font-size:12px; border-radius:99px; padding:3px 9px; background:#eee8dc; color:var(--muted); }
  .pill.yes { background:#e2f0e8; color:var(--green); }
  .pill.no { background:#f5e5e2; color:var(--red); }
  .links { font-size:12px; color:var(--muted); display:flex; flex-direction:column; gap:5px; }
  a { color:var(--green); }
  .toast { position:fixed; left:50%; bottom:22px; transform:translateX(-50%); background:#2b2b2b; color:#fff; padding:8px 14px; border-radius:9px; font-size:13px; opacity:0; pointer-events:none; transition:opacity .2s; z-index:9; }
  .toast.show { opacity:1; }
</style>
</head>
<body>
<header>
  <h1>腿部并拢 · 点击标注</h1>
  <span class="sub">只看“刻意并拢”，自然站直靠近不算</span>
  <span id="sheetLink" class="sub"></span>
</header>
<main>
  <section class="stage">
    <div class="bar">
      <strong id="caseNo">#001</strong>
      <span id="labelState" class="pill">未标注</span>
      <div class="progress"><i id="progressBar"></i></div>
      <span id="progressText">0 / 200</span>
    </div>
    <div class="photo-wrap"><img id="photo" alt="待标注图片"></div>
    <div class="toolbar">
      <button class="primary" id="yesBtn">✓ 是并拢 <span class="hint">1</span></button>
      <button class="danger" id="noBtn">✗ 不是 <span class="hint">0</span></button>
      <button class="ghost" id="prevBtn">← 上一张</button>
      <button class="ghost" id="nextBtn">下一张 →</button>
      <label class="hint"><input type="checkbox" id="autoNext" checked> 自动下一张</label>
    </div>
  </section>
  <aside>
    <div class="card">
      <h2>标注进度</h2>
      <div class="stat"><span>已标注</span><b id="labeledCount">0</b></div>
      <div class="stat"><span>其中“是”</span><b id="yesCount">0</b></div>
      <div class="stat"><span>其中“不是”</span><b id="noCount">0</b></div>
    </div>
    <div class="card">
      <h2>备注（可选）</h2>
      <textarea id="notes" placeholder="不确定/多图拼贴/特殊姿势等"></textarea>
      <div class="hint" style="margin-top:6px">自动保存在浏览器本地</div>
    </div>
    <div class="card">
      <h2>数据</h2>
      <div class="links">
        <button id="exportCsv">导出 CSV</button>
        <button id="exportJson">导出 JSON</button>
        <button id="jumpUnlabeled" class="ghost">跳到第一张未标注</button>
        <button id="reset" class="ghost">清空本地标注</button>
      </div>
    </div>
    <div class="card">
      <h2>判定口径</h2>
      <div style="font-size:13px;color:var(--muted);line-height:1.7">
        true 需要同时满足：全身、双腿双脚完整可见；双脚/双膝明显主动靠拢；刻意姿态（站立主动并拢/跪姿/蹲姿/坐姿双膝并拢）。
        交叉腿、二郎腿、分开、抬腿、仰躺、普通站直、半身/七分、多图姿态不一致，一律 false。
      </div>
    </div>
  </aside>
</main>
<div id="toast" class="toast"></div>
<script>
const DATA = __DATA__;
const KEY = "leg-posture-gold-20261124-v1";
let state = {labels:{}, notes:{}};
try { state = Object.assign({labels:{},notes:{}}, JSON.parse(localStorage.getItem(KEY) || "{}")); } catch (e) {}
DATA.forEach(r => { if (r.initial !== null && state.labels[r.no] === undefined) state.labels[r.no] = r.initial; });
let idx = Math.max(0, DATA.findIndex(r => state.labels[r.no] === undefined));
const $ = id => document.getElementById(id);
function save(){ localStorage.setItem(KEY, JSON.stringify(state)); }
function toast(msg){ const t=$("toast"); t.textContent=msg; t.classList.add("show"); setTimeout(()=>t.classList.remove("show"),1200); }
function current(){ return DATA[idx]; }
function render(){
  const r = current();
  $("caseNo").textContent = "#" + String(r.no).padStart(3,"0");
  const photo = $("photo"); photo.src = r.image;
  $("photo").onerror = () => { $("photo").alt = "图片加载失败：" + r.image; };
  const sheet = $("sheetLink"); sheet.innerHTML = "";
  const a = document.createElement("a"); a.href=r.sheet; a.textContent="查看联系人表 " + r.sheet_name; sheet.appendChild(a);
  const v = state.labels[r.no];
  $("labelState").className = "pill " + (v === true ? "yes" : v === false ? "no" : "");
  $("labelState").textContent = v === true ? "是并拢" : v === false ? "不是并拢" : "未标注";
  $("notes").value = state.notes[r.no] || "";
  $("notes").oninput = () => { state.notes[r.no] = $("notes").value; save(); };
  const labeled = DATA.filter(x => state.labels[x.no] !== undefined);
  $("labeledCount").textContent = labeled.length;
  $("yesCount").textContent = labeled.filter(x => state.labels[x.no] === true).length;
  $("noCount").textContent = labeled.filter(x => state.labels[x.no] === false).length;
  $("progressText").textContent = labeled.length + " / " + DATA.length;
  $("progressBar").style.width = (labeled.length / DATA.length * 100) + "%";
  $("prevBtn").disabled = idx === 0;
  $("nextBtn").disabled = idx === DATA.length - 1;
  // 预加载下一张
  if (idx + 1 < DATA.length) { const im = new Image(); im.src = DATA[idx+1].image; }
}
function go(delta){
  idx = Math.min(DATA.length-1, Math.max(0, idx + delta));
  render();
}
function nextUnlabeled(){
  for (let i=0;i<DATA.length;i++){
    const j=(idx+1+i)%DATA.length;
    if (state.labels[DATA[j].no] === undefined){ idx=j; render(); return; }
  }
  toast("全部已标注，可导出结果");
}
function setLabel(v){
  const r=current(); state.labels[r.no]=v; save(); render();
  if ($("autoNext").checked) setTimeout(nextUnlabeled, 180);
}
$("yesBtn").onclick = () => setLabel(true);
$("noBtn").onclick = () => setLabel(false);
$("prevBtn").onclick = () => go(-1);
$("nextBtn").onclick = () => go(1);
$("jumpUnlabeled").onclick = () => { idx=DATA.findIndex(r=>state.labels[r.no]===undefined); idx=Math.max(0,idx); render(); };
$("reset").onclick = () => { if (confirm("清空浏览器本地标注？不会影响已导出的文件。")) { state={labels:{},notes:{}}; save(); idx=0; render(); } };
function download(name, text){
  const blob=new Blob([text],{type:"text/csv;charset=utf-8"});
  const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}
$("exportCsv").onclick = () => {
  const lines=["gold_no,meaningful_together,notes"];
  DATA.forEach(r=>{ const v=state.labels[r.no]; if(v===undefined) return; const n=(state.notes[r.no]||"").replace(/"/g,'""'); lines.push(`${r.no},${v},"${n}"`); });
  download("leg-together-labels-"+new Date().toISOString().slice(0,10)+".csv", lines.join("\n"));
  toast("已导出 CSV");
};
$("exportJson").onclick = () => {
  const rows=DATA.map(r=>({gold_no:r.no,meaningful_together:state.labels[r.no]??null,notes:state.notes[r.no]||""}));
  download("leg-together-labels-"+new Date().toISOString().slice(0,10)+".json", JSON.stringify(rows,null,2));
  toast("已导出 JSON");
};
document.addEventListener("keydown", e=>{
  if (e.target && (e.target.tagName==="TEXTAREA"||e.target.tagName==="INPUT")) return;
  if (e.key==="1" || e.key.toLowerCase()==="y") setLabel(true);
  else if (e.key==="0" || e.key.toLowerCase()==="n") setLabel(false);
  else if (e.key==="ArrowLeft") go(-1);
  else if (e.key==="ArrowRight") go(1);
});
render();
</script>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    gold_dir = args.gold_dir
    labels = _existing_labels(gold_dir)
    records = _records(gold_dir, labels)
    html = HTML.replace("__DATA__", json.dumps(records, ensure_ascii=False))
    output = args.output or gold_dir / "annotate.html"
    output.write_text(html, encoding="utf-8")
    print(f"已生成：{output}")
    print(f"已加载已有标注：{len(labels)} / {len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

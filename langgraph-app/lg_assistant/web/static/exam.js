/* Small companion to the existing composer; all reference text is rendered as text. */
(() => {
  const $ = (id) => document.getElementById(id);
  let owner = "local", selected = new Set(), loaded = false, publicLookupPreferred = true;
  const status = (text) => { $("exam-library-status").textContent = text; };
  async function api(path, init) {
    const r = await fetch(path, init);
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "资料操作失败，请重试");
    return data;
  }
  function policy() {
    const internal = $("exam-profile").value === "internal";
    $("exam-public").disabled = internal || selected.size > 0 || $("exam-grounded").checked;
    $("exam-web").disabled = internal || selected.size > 0;
    $("exam-web").checked = !$("exam-web").disabled && publicLookupPreferred;
    $("exam-grounded").disabled = internal || selected.size > 0;
    if (internal || selected.size) $("exam-grounded").checked = true;
    $("exam-library-count").textContent = selected.size ? `已选 ${selected.size} 份` : "未选择";
    $("exam-policy").textContent = internal || selected.size
      ? "使用所选资料与原图核对。当前不联网检索；缺少有效依据时会指出需要补充的内容。"
      : "识题后按题型解答，计算与原图分别核对；具体错误最多自动纠正一次。";
  }
  function options() {
    return {profile: $("exam-profile").value, exam_date: $("exam-date").value.trim(),
      document_ids: [...selected], require_knowledge: $("exam-grounded").checked,
      use_public_knowledge: $("exam-public").checked && !$("exam-public").disabled,
      allow_web: $("exam-web").checked && !$("exam-web").disabled};
  }
  async function refresh(nextOwner = owner) {
    if (owner !== nextOwner) { selected.clear(); loaded = false; }
    owner = nextOwner;
    try {
      const data = await api("/api/exam/knowledge?owner=" + encodeURIComponent(owner));
      const existing = new Set(data.documents.map(d => d.id));
      selected = new Set([...selected].filter(id => existing.has(id)));
      const host = $("exam-documents"); host.replaceChildren();
      if (!data.documents.length) {
        const p = document.createElement("p"); p.textContent = "暂无资料"; host.append(p);
      }
      data.documents.forEach(doc => {
        const row = document.createElement("div"); row.className = "exam-doc";
        const label = document.createElement("label");
        const check = document.createElement("input"); check.type = "checkbox"; check.checked = selected.has(doc.id);
        check.addEventListener("change", () => {
          if (check.checked && selected.size >= 20) { check.checked = false; status("最多选择20份资料"); return; }
          check.checked ? selected.add(doc.id) : selected.delete(doc.id); policy();
        });
        const title = document.createElement("span"); title.textContent = doc.title;
        const meta = document.createElement("small"); meta.textContent = `${doc.version || "未注明版本"} · ${doc.chars.toLocaleString()} 字`;
        title.append(meta); label.append(check, title);
        const actions = document.createElement("div"); actions.className = "exam-doc-actions";
        const view = document.createElement("button"); view.type = "button"; view.className = "btn btn-ghost btn-sm"; view.textContent = "查看原文";
        view.setAttribute("aria-label", "查看原文：" + doc.title);
        view.addEventListener("click", async () => {
          const old = row.querySelector("pre"); if (old) { old.remove(); return; }
          view.disabled = true;
          try {
            const d = await api(`/api/exam/knowledge/${doc.id}?owner=${encodeURIComponent(owner)}`);
            const pre = document.createElement("pre"); pre.textContent = d.content; row.append(pre);
          } catch(e) { status(e.message); } finally { view.disabled = false; }
        });
        const del = document.createElement("button"); del.type = "button"; del.className = "btn btn-ghost btn-sm"; del.textContent = "删除";
        del.setAttribute("aria-label", "删除资料：" + doc.title);
        del.addEventListener("click", async () => {
          if (!confirm(`删除《${doc.title}》？已归档答案中的引用仍会保留。`)) return;
          del.disabled = true;
          try { await api(`/api/exam/knowledge/${doc.id}?owner=${encodeURIComponent(owner)}`, {method:"DELETE"}); selected.delete(doc.id); await refresh(); status("资料已删除"); }
          catch(e) { status(e.message); del.disabled = false; }
        });
        actions.append(view, del); row.append(label, actions); host.append(row);
      });
      loaded = true; policy();
    } catch(e) { status(e.message); }
  }
  $("exam-profile").addEventListener("change", () => {
    $("exam-grounded").checked = $("exam-profile").value === "internal"; policy();
  });
  $("exam-web").addEventListener("change", () => { publicLookupPreferred = $("exam-web").checked; });
  $("exam-grounded").addEventListener("change", policy);
  $("exam-library").addEventListener("toggle", () => { if ($("exam-library").open && !loaded) refresh(); });
  $("exam-import-form").addEventListener("submit", async (e) => {
    e.preventDefault(); const button = e.submitter; button.disabled = true; status("正在导入资料…");
    try {
      const fd = new FormData(); fd.set("owner",owner); fd.set("title",$("exam-doc-title").value); fd.set("version",$("exam-doc-version").value); fd.set("content",$("exam-doc-content").value);
      const file = $("exam-doc-file").files[0];
      if (file && file.size > 2 * 1024 * 1024) throw new Error("文件最多2MB，请拆分后导入");
      if (file) fd.set("file", file);
      const d = await api("/api/exam/knowledge", {method:"POST", body:fd});
      if (selected.size < 20) selected.add(d.id);
      e.target.reset(); await refresh(); status(d.duplicate ? "相同资料已存在，已保留原版本" : "资料已保存，可在上方勾选使用");
    } catch(e) { status(e.message); } finally { button.disabled = false; }
  });
  window.Exam = {options, refresh};
  async function loadPublicCatalog() {
    try {
      const catalog = await api("/api/exam/public-knowledge");
      $("exam-public-status").textContent = `内置 ${catalog.count} 个知识摘要与方法，覆盖 ${Object.keys(catalog.modules).length} 个模块。${catalog.scope}`;
      const host = $("exam-public-modules"); host.replaceChildren();
      const list = document.createElement("ul");
      Object.entries(catalog.modules).forEach(([name, count]) => {
        const item = document.createElement("li");
        const module = document.createElement("details");
        const heading = document.createElement("summary"); heading.textContent = `${name} · ${count} 项`;
        module.append(heading);
        (catalog.concepts || []).filter(note => note.module === name).forEach(note => {
          const entry = document.createElement("details");
          const title = document.createElement("summary"); title.textContent = note.title;
          const content = document.createElement("p"); content.textContent = note.content;
          const version = document.createElement("small");
          version.textContent = `${note.version_label}；${note.source_kind === "scope" ? "独立整理的方法，官方大纲仅界定范围" : "有版本依据的概念摘要"}`;
          entry.append(title, content, version); module.append(entry);
        });
        item.append(module); list.append(item);
      });
      host.append(list);
      const sources = document.createElement("details");
      const summary = document.createElement("summary"); summary.textContent = "资料来源与版本说明"; sources.append(summary);
      catalog.sources.forEach(source => {
        const paragraph = document.createElement("p");
        const link = document.createElement("a"); link.textContent = source.title;
        const url = new URL(source.url); if (url.protocol !== "https:") return;
        link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer";
        paragraph.append(link, document.createTextNode(`：${source.note}`)); sources.append(paragraph);
      });
      host.append(sources);
    } catch (e) { $("exam-public-status").textContent = "知识目录读取失败，可刷新页面重试。"; }
  }
  loadPublicCatalog();
  policy();
})();

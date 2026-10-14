const state = {
  projects: [],
  current: null,
  settings: null,
  health: null,
  tab: "script",
  error: "",
  notice: "",
};

const SOURCE = { builtin: "自带", external: "外部", import: "导入" };
const STATUS = { empty: "还没有画面", queued: "排队", running: "正在生成", ready: "已就绪", failed: "失败" };
const KIND = { character: "角色", location: "场景", prop: "道具" };

function yuan(cents) {
  return (Number(cents || 0) / 100).toFixed(2) + " 元";
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function jsonOptions(method, body) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  };
}

async function refreshList() {
  const data = await api("/api/projects");
  state.projects = data.projects;
}

async function openProject(id) {
  state.current = await api("/api/projects/" + id);
  state.error = "";
  paint();
}

async function boot() {
  state.health = await api("/api/health");
  state.settings = await api("/api/settings");
  await refreshList();
  paint();
}

function banners() {
  const parts = [];
  if (state.health && state.health.ffmpeg === false) {
    parts.push(`<div class="banner bad">没有找到 ffmpeg。安装之后才能生成和导入画面。</div>`);
  }
  if (state.error) parts.push(`<div class="banner bad">${escapeHtml(state.error)}</div>`);
  if (state.notice) parts.push(`<div class="banner">${escapeHtml(state.notice)}</div>`);
  return parts.join("");
}

function busy() {
  return (state.current?.shots || []).some((shot) => shot.status === "queued" || shot.status === "running");
}

function paint() {
  document.getElementById("rail").innerHTML = railHtml();
  document.getElementById("main").innerHTML = mainHtml();
  const video = document.querySelector("video");
  if (video) video.load();
}

function railHtml() {
  const items = state.projects.map((project) => {
    const on = state.current && state.current.project.id === project.id ? " on" : "";
    return `<button class="project${on}" data-act="open" data-id="${project.id}">
      <strong>${escapeHtml(project.name)}</strong>
      <div class="muted">${project.ready_count}/${project.shot_count} 条就绪</div>
    </button>`;
  }).join("");
  return `<div class="row">
      <button class="primary" data-act="create">新项目</button>
      <button data-act="sample">载入示例</button>
    </div>
    ${items || '<p class="muted">还没有项目。</p>'}`;
}

function mainHtml() {
  if (!state.current) {
    return `${banners()}<section class="panel"><h2>先建一个项目</h2>
      <p class="muted">示例「雨夜送伞」有三个镜头：自带推拉、外部占位、等待导入。通过剧本并锁定资产后，才能生成前两条。</p></section>`;
  }
  const project = state.current.project;
  const gates = state.current.gates;
  return `${banners()}
    <section class="panel">
      <div class="row">
        <h2>${escapeHtml(project.name)}</h2>
        <span class="pill ${gates.script_approved ? "ready" : ""}">${gates.script_approved ? "剧本已通过" : "剧本未通过"}</span>
        <span class="pill ${gates.assets_locked ? "ready" : ""}">${gates.assets_locked ? "资产已锁定" : "资产未锁定"}</span>
        <span class="pill">${gates.ready}/${gates.total} 条画面</span>
        <span class="pill">估价 ${yuan(gates.cost_cents)}</span>
      </div>
      <p class="muted">${escapeHtml(project.logline || "还没有一句话简介")}</p>
      <div class="row tabs">
        ${tabButton("script", "剧本")}
        ${tabButton("assets", "资产")}
        ${tabButton("shots", "分镜")}
        ${tabButton("film", "成片")}
        ${tabButton("settings", "设置")}
      </div>
      ${panelHtml()}
    </section>`;
}

function tabButton(id, label) {
  return `<button data-act="tab" data-tab="${id}" class="${state.tab === id ? "on" : ""}">${label}</button>`;
}

function panelHtml() {
  if (state.tab === "script") return scriptHtml();
  if (state.tab === "assets") return assetsHtml();
  if (state.tab === "shots") return shotsHtml();
  if (state.tab === "film") return filmHtml();
  return settingsHtml();
}

function scriptHtml() {
  const project = state.current.project;
  return `<label>项目名<input id="name" value="${escapeAttr(project.name)}"></label>
    <label>一句话<input id="logline" value="${escapeAttr(project.logline)}"></label>
    <label>剧本<textarea id="script">${escapeHtml(project.script)}</textarea></label>
    <pre class="help">空行分段。每一段可以写：
画面：雨夜巷口
对白：伞还在店里。
角色：阿宁
运镜：缓慢推近
时长：5秒</pre>
    <div class="row">
      <button data-act="save-script">保存剧本</button>
      <button data-act="split">拆成镜头</button>
      <button class="primary" data-act="approve">通过剧本</button>
      <button data-act="reopen">退回修改</button>
      <button class="danger" data-act="delete-project">删除项目</button>
    </div>`;
}

function assetsHtml() {
  const cards = state.current.assets.map((asset) => {
    const image = asset.image_path ? `<img class="portrait" src="/files/${asset.image_path}" alt="">` : "";
    return `<article class="asset">
      ${image}
      <div class="split">
        <label>类型<select data-field="kind">${options(KIND, asset.kind)}</select></label>
        <label>名字<input data-field="name" value="${escapeAttr(asset.name)}"></label>
      </div>
      <label>备注<input data-field="note" value="${escapeAttr(asset.note)}"></label>
      <div class="row">
        <button data-act="save-asset" data-id="${asset.id}">保存</button>
        <label class="file">上传参考图<input data-act="asset-image" data-id="${asset.id}" type="file" accept="image/*" hidden></label>
        <button class="danger" data-act="delete-asset" data-id="${asset.id}">删除</button>
      </div>
    </article>`;
  }).join("");
  return `<p class="muted">角色名写进镜头的「角色」或画面说明后，自带画面会带上参考图。改动资产会取消锁定。</p>
    <div class="split">
      <label>类型<select id="asset-kind">${options(KIND, "character")}</select></label>
      <label>名字<input id="asset-name" placeholder="阿宁"></label>
    </div>
    <label>备注<input id="asset-note"></label>
    <div class="row">
      <button data-act="add-asset">添加资产</button>
      <button class="primary" data-act="lock">锁定资产</button>
      <button data-act="unlock">取消锁定</button>
    </div>
    ${cards || '<p class="muted">还没有资产。</p>'}`;
}

function gateNotes() {
  const gates = state.current.gates;
  if (!gates.script_approved) {
    return `<div class="banner">先在剧本页通过剧本。通过之前不能生成，也不能导入。</div>`;
  }
  if (!gates.assets_locked) {
    return `<div class="banner">资产还没锁定。自带和外部镜头要先锁定；导入视频可以现在上传。</div>`;
  }
  return "";
}

function shotsHtml() {
  const cards = state.current.shots.map((shot) => shotHtml(shot)).join("");
  return `${gateNotes()}<div class="row">
      <button data-act="add-shot">加一条镜头</button>
      <button class="primary" data-act="generate-all" ${busy() ? "disabled" : ""}>生成未完成的自带和外部镜头</button>
    </div>
    <p class="muted">自带生成是静帧推拉，不下载模型。外部接口默认也画一张占位画面；在设置里改成 http 才会访问你填的地址。导入镜头要自己选文件。</p>
    ${cards || '<p class="muted">先在剧本页拆镜头。</p>'}`;
}

function shotHtml(shot) {
  const stale = shot.status === "ready" && shot.generated_at && shot.updated_at > shot.generated_at;
  const pillClass = shot.status === "ready" ? "ready" : shot.status === "failed" ? "bad" : shot.status === "queued" || shot.status === "running" ? "busy" : "";
  return `<article class="shot" data-shot="${shot.id}">
    <div class="row">
      <strong>镜头 ${shot.position + 1}</strong>
      <span class="pill ${shot.source}">${SOURCE[shot.source] || shot.source}</span>
      <span class="pill ${pillClass}">${STATUS[shot.status] || shot.status}</span>
      <span class="pill">${(shot.duration_ms / 1000).toFixed(1)} 秒</span>
      <span class="pill">${yuan(shot.cost_cents)}${shot.cost_note ? " · " + escapeHtml(shot.cost_note) : ""}</span>
    </div>
    ${stale ? '<p class="muted">文案已经改过，画面还是上一次的。</p>' : ""}
    ${shot.error ? `<p class="banner bad">${escapeHtml(shot.error)}</p>` : ""}
    ${shot.voice_note ? `<p class="muted">${escapeHtml(shot.voice_note)}</p>` : ""}
    <div class="shot-grid">
      <div>
        <label>画面<input data-field="visual_prompt" value="${escapeAttr(shot.visual_prompt)}"></label>
        <label>对白<input data-field="dialogue" value="${escapeAttr(shot.dialogue)}"></label>
        <div class="split">
          <label>角色<input data-field="cast" value="${escapeAttr(shot.cast)}"></label>
          <label>运镜<input data-field="camera" value="${escapeAttr(shot.camera)}"></label>
        </div>
        <div class="split">
          <label>时长（秒）<input data-field="seconds" type="number" min="1" max="180" step="1" value="${Math.round(shot.duration_ms / 1000)}"></label>
          <label>来源<select data-field="source">${options(SOURCE, shot.source)}</select></label>
        </div>
      </div>
      <div>
        ${shot.file_path ? `<video src="/files/${shot.file_path}" controls></video>` : '<div class="help">这条还没有画面。</div>'}
      </div>
    </div>
    <div class="row">
      <button data-act="save-shot" data-id="${shot.id}">保存这条</button>
      ${shot.source === "import"
        ? `<label class="file">导入视频<input data-act="import-shot" data-id="${shot.id}" type="file" accept="video/*" hidden></label>`
        : `<button data-act="generate-shot" data-id="${shot.id}" ${busy() ? "disabled" : ""}>${shot.status === "ready" ? "重做这条" : "生成这条"}</button>`}
      <button data-act="move" data-id="${shot.id}" data-dir="up">上移</button>
      <button data-act="move" data-id="${shot.id}" data-dir="down">下移</button>
      <button class="danger" data-act="delete-shot" data-id="${shot.id}">删除</button>
    </div>
  </article>`;
}

function filmHtml() {
  const exp = state.current.export;
  const shots = state.current.shots;
  const missing = shots.filter((shot) => shot.status !== "ready" || !shot.file_path);
  const rows = shots.map((shot) => {
    const ready = shot.status === "ready" && shot.file_path;
    const label = ready ? "已就绪" : (STATUS[shot.status] || "还没有画面");
    return `<li>镜头 ${shot.position + 1} · ${SOURCE[shot.source] || shot.source} · ${label} · ${(shot.duration_ms / 1000).toFixed(1)} 秒</li>`;
  }).join("");
  const preview = exp && exp.preview_path ? `<video src="/files/${exp.preview_path}" controls></video>` : '<p class="muted">还没有预览。</p>';
  const blocked = missing.length ? `<p class="muted">还不能合成：${missing.map((shot) => "镜头 " + (shot.position + 1)).join("、")} 没有画面。</p>` : "";
  return `<p class="muted">每条镜头都有画面之后，才能合成。剪映草稿要先在设置里填写本机草稿目录。没填也能下载时间轴包。</p>
    ${blocked}
    <ul class="timeline">${rows}</ul>
    ${exp && exp.message ? `<div class="banner">${escapeHtml(exp.message)}</div>` : ""}
    ${preview}
    <div class="row">
      <button class="primary" data-act="export" ${busy() || !shots.length || missing.length ? "disabled" : ""}>合成并导出</button>
      <button data-act="reveal">打开导出目录</button>
      ${exp && exp.preview_path ? `<a href="/files/${exp.preview_path}" download>下载成片</a>` : ""}
      ${exp && exp.srt_path ? `<a href="/files/${exp.srt_path}" download>字幕</a>` : ""}
      ${exp && exp.package_dir ? `<a href="/files/${exp.package_dir}/timeline.json">时间轴</a>` : ""}
    </div>
    <div class="row">
      <label class="file">背景音乐<input data-act="bgm" type="file" accept="audio/*" hidden></label>
      ${state.current.project.bgm_path ? `<button data-act="clear-bgm">去掉背景音乐</button>` : ""}
    </div>
    <p class="muted">${state.current.project.bgm_path ? "已有背景音乐，合成时会压低混进成片。" : "可选。上传后只混进成片，不改每一条镜头。"}</p>`;
}

function settingsHtml() {
  const settings = state.settings || {};
  return `<p class="muted">外部接口默认是占位，不会出网。改成 http 后，工作台向你的地址提交任务再轮询结果。</p>
    <label>模式
      <select id="external_mode">
        <option value="stub" ${settings.external_mode !== "http" ? "selected" : ""}>占位，本地画一张外部镜头</option>
        <option value="http" ${settings.external_mode === "http" ? "selected" : ""}>http，提交并轮询</option>
      </select>
    </label>
    <label>接口地址<input id="external_base_url" placeholder="https://example.com" value="${escapeAttr(settings.external_base_url || "")}"></label>
    <label>模型名<input id="external_model" value="${escapeAttr(settings.external_model || "")}"></label>
    <label>密钥<input id="external_api_key" type="password" placeholder="${settings.external_api_key_set ? "已保存，留空则不改" : "没有密钥"}"></label>
    <label>外部镜头估价（分/秒）<input id="external_cents_per_second" type="number" min="0" value="${Number(settings.external_cents_per_second || 0)}"></label>
    <label>剪映草稿目录<input id="jianying_draft_dir" placeholder="C:\\Users\\你\\AppData\\Local\\JianyingPro\\User Data\\Projects\\com.lveditor.draft" value="${escapeAttr(settings.jianying_draft_dir || "")}"></label>
    ${settings.jianying_suggested && settings.jianying_suggested !== settings.jianying_draft_dir ? `<button data-act="use-jianying">填入本机剪映目录</button>` : ""}
    <label>配音
      <select id="tts_enabled">
        <option value="true" ${settings.tts_enabled ? "selected" : ""}>用 Edge TTS，失败就改静音</option>
        <option value="false" ${settings.tts_enabled ? "" : "selected"}>关闭，只用静音</option>
      </select>
    </label>
    <label>Edge 音色<input id="tts_voice" value="${escapeAttr(settings.tts_voice || "")}"></label>
    <pre class="help">http 约定
POST {地址}/v1/videos
{"model","prompt","duration_ms","aspect":"9:16"}
返回 {"task_id"} 或直接 {"status":"ready","url"}
GET {地址}/v1/videos/{task_id}
返回 {"status":"queued|running|ready|failed","url","error"}</pre>
    <button class="primary" data-act="save-settings">保存设置</button>`;
}

function options(map, selected) {
  return Object.entries(map).map(([value, label]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`).join("");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function shotFields(article) {
  const seconds = Number(article.querySelector('[data-field="seconds"]').value || 5);
  return {
    visual_prompt: article.querySelector('[data-field="visual_prompt"]').value,
    dialogue: article.querySelector('[data-field="dialogue"]').value,
    cast: article.querySelector('[data-field="cast"]').value,
    camera: article.querySelector('[data-field="camera"]').value,
    duration_ms: Math.round(seconds * 1000),
    source: article.querySelector('[data-field="source"]').value,
    title: (article.querySelector('[data-field="visual_prompt"]').value || "").slice(0, 18),
  };
}

async function act(name, target) {
  state.error = "";
  const id = target.dataset.id;
  try {
    if (name === "create") {
      const created = await api("/api/projects", jsonOptions("POST", { name: "未命名项目" }));
      await refreshList();
      state.current = created;
      state.tab = "script";
    } else if (name === "sample") {
      state.current = await api("/api/sample", jsonOptions("POST", {}));
      await refreshList();
      state.tab = "script";
      state.notice = "示例已载入。先通过剧本，再锁定资产，然后生成前两条。第三条需要导入一个视频。";
    } else if (name === "open") {
      await openProject(id);
      return;
    } else if (name === "tab") {
      state.tab = target.dataset.tab;
    } else if (name === "save-script") {
      state.current = await api("/api/projects/" + state.current.project.id, jsonOptions("PUT", {
        name: document.getElementById("name").value,
        logline: document.getElementById("logline").value,
        script: document.getElementById("script").value,
      }));
      state.notice = "剧本已保存。改过正文后需要重新通过。";
      await refreshList();
    } else if (name === "split") {
      state.current = await api("/api/projects/" + state.current.project.id + "/split", jsonOptions("POST"));
      state.tab = "shots";
      state.notice = "已按空行拆成镜头。原来的镜头被替换了。";
    } else if (name === "approve") {
      state.current = await api("/api/projects/" + state.current.project.id + "/approve", jsonOptions("POST"));
      state.notice = "剧本已通过。";
    } else if (name === "reopen") {
      state.current = await api("/api/projects/" + state.current.project.id + "/reopen", jsonOptions("POST"));
    } else if (name === "delete-project") {
      if (!confirm("删除这个项目？画面文件会一起删掉。")) return;
      await api("/api/projects/" + state.current.project.id, { method: "DELETE" });
      state.current = null;
      await refreshList();
    } else if (name === "add-asset") {
      state.current = await api("/api/projects/" + state.current.project.id + "/assets", jsonOptions("POST", {
        kind: document.getElementById("asset-kind").value,
        name: document.getElementById("asset-name").value,
        note: document.getElementById("asset-note").value,
      }));
    } else if (name === "save-asset") {
      const card = target.closest(".asset");
      state.current = await api("/api/assets/" + id, jsonOptions("PUT", {
        kind: card.querySelector('[data-field="kind"]').value,
        name: card.querySelector('[data-field="name"]').value,
        note: card.querySelector('[data-field="note"]').value,
      }));
    } else if (name === "delete-asset") {
      state.current = await api("/api/assets/" + id, { method: "DELETE" });
    } else if (name === "lock" || name === "unlock") {
      state.current = await api("/api/projects/" + state.current.project.id + "/lock", jsonOptions("POST", { locked: name === "lock" }));
    } else if (name === "add-shot") {
      state.current = await api("/api/projects/" + state.current.project.id + "/shots", jsonOptions("POST", {}));
    } else if (name === "save-shot") {
      const fields = shotFields(target.closest(".shot"));
      state.current = await api("/api/shots/" + id, jsonOptions("PUT", fields));
    } else if (name === "delete-shot") {
      state.current = await api("/api/shots/" + id, { method: "DELETE" });
    } else if (name === "move") {
      state.current = await api("/api/shots/" + id + "/move", jsonOptions("POST", { direction: target.dataset.dir }));
    } else if (name === "generate-shot") {
      const fields = shotFields(target.closest(".shot"));
      await api("/api/shots/" + id, jsonOptions("PUT", fields));
      state.current = await api("/api/shots/" + id + "/generate", jsonOptions("POST"));
      state.notice = "已开始生成这一条。";
    } else if (name === "generate-all") {
      for (const article of document.querySelectorAll(".shot")) {
        await api("/api/shots/" + article.dataset.shot, jsonOptions("PUT", shotFields(article)));
      }
      const result = await api("/api/projects/" + state.current.project.id + "/generate", jsonOptions("POST"));
      state.current = result;
      const skip = (result.skipped || []).map((item) => `镜头 ${item.shot}：${item.reason}`).join(" ");
      state.notice = `已排队 ${ (result.started || []).length } 条。${skip}`;
    } else if (name === "export") {
      state.notice = "正在合成，视频长一点就会多等几秒。";
      paint();
      state.current = await api("/api/projects/" + state.current.project.id + "/export", jsonOptions("POST"));
      state.notice = state.current.message || "已导出。";
      state.tab = "film";
    } else if (name === "reveal") {
      await api("/api/projects/" + state.current.project.id + "/reveal", jsonOptions("POST"));
    } else if (name === "clear-bgm") {
      state.current = await api("/api/projects/" + state.current.project.id + "/bgm", { method: "DELETE" });
      state.notice = "背景音乐已去掉。下次合成不会再混进去。";
    } else if (name === "use-jianying") {
      state.settings = Object.assign({}, state.settings, { jianying_draft_dir: state.settings.jianying_suggested || "" });
      state.notice = "已填入本机剪映草稿目录。点保存设置后，导出才会写草稿。";
    } else if (name === "save-settings") {
      state.settings = await api("/api/settings", jsonOptions("PUT", {
        external_mode: document.getElementById("external_mode").value,
        external_base_url: document.getElementById("external_base_url").value,
        external_model: document.getElementById("external_model").value,
        external_api_key: document.getElementById("external_api_key").value,
        external_cents_per_second: Number(document.getElementById("external_cents_per_second").value || 0),
        jianying_draft_dir: document.getElementById("jianying_draft_dir").value,
        tts_enabled: document.getElementById("tts_enabled").value === "true",
        tts_voice: document.getElementById("tts_voice").value,
      }));
      state.notice = "设置已保存。";
    }
  } catch (error) {
    state.error = error.message;
  }
  await refreshList();
  paint();
}

document.body.addEventListener("click", (event) => {
  const button = event.target.closest("[data-act]");
  if (!button || button.tagName === "INPUT") return;
  act(button.dataset.act, button);
});

document.body.addEventListener("change", async (event) => {
  const input = event.target;
  if (input.dataset.act === "import-shot") {
    const file = input.files && input.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    try {
      state.current = await api("/api/shots/" + input.dataset.id + "/import", { method: "POST", body });
      state.notice = "视频已导入这条镜头。";
      state.error = "";
    } catch (error) {
      state.error = error.message;
    }
    await refreshList();
    paint();
  }
  if (input.dataset.act === "asset-image" || input.dataset.act === "bgm") {
    const file = input.files && input.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    const path = input.dataset.act === "bgm"
      ? "/api/projects/" + state.current.project.id + "/bgm"
      : "/api/assets/" + input.dataset.id + "/image";
    try {
      state.current = await api(path, { method: "POST", body });
      state.error = "";
      state.notice = input.dataset.act === "bgm" ? "背景音乐已保存。" : "参考图已保存。资产锁定已取消。";
    } catch (error) {
      state.error = error.message;
    }
    paint();
  }
});

setInterval(async () => {
  if (!state.current || !busy()) return;
  if (document.activeElement && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  try {
    state.current = await api("/api/projects/" + state.current.project.id);
    await refreshList();
    paint();
  } catch (error) {
    state.error = error.message;
  }
}, 1200);

boot().catch((error) => {
  state.error = error.message;
  paint();
});

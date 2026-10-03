"use strict";

// ---- DOM 参照 ----
const els = {
  statusLlm: document.getElementById("status-llm"),
  statusTts: document.getElementById("status-tts"),
  statusStt: document.getElementById("status-stt"),
  selectA: document.getElementById("select-a"),
  selectB: document.getElementById("select-b"),
  topic: document.getElementById("topic"),
  maxTurns: document.getElementById("max-turns"),
  form: document.getElementById("dialogue-form"),
  startButton: document.getElementById("start-button"),
  stopButton: document.getElementById("stop-button"),
  notice: document.getElementById("notice"),
  logList: document.getElementById("log-list"),
  logEmpty: document.getElementById("log-empty"),
  bridge: document.getElementById("bridge"),
  bridgeStatus: document.getElementById("bridge-status"),
  personaA: document.getElementById("persona-a"),
  personaB: document.getElementById("persona-b"),
  nameA: document.getElementById("persona-a-name"),
  nameB: document.getElementById("persona-b-name"),
  idA: document.getElementById("persona-a-id"),
  idB: document.getElementById("persona-b-id"),
  connected: document.getElementById("connected"),
  runModeRadios: document.querySelectorAll('input[name="run-mode"]'),
  fieldB: document.getElementById("field-b"),
  fieldTurns: document.getElementById("field-turns"),
  fieldOutput: document.getElementById("field-output"),
  outputDevice: document.getElementById("output-device"),
  selectALabel: document.getElementById("select-a-label"),
  topicLabel: document.getElementById("topic-label"),
};

// 稼働状態を人間語に翻訳する。
const STATUS_LABEL = {
  configured: "稼働",
  "not-configured": "未設定",
  unknown: "確認中",
};

let devices = [];
let audioContext = null;
let currentSource = null;
let running = false;
let stopRequested = false;
let relayPollTimer = null;
let connectedDevices = [];
let runningMode = null; // 実行中のモード（"browser" | "relay" | "chat"）
// 「実機に話しかける」の会話履歴キー。ページを開いている間は同じ履歴で会話が続く。
const chatSessionId = `webchat-${Date.now()}`;
// relay の出力先セレクトで「各ペルソナの実機（2台）」を表す値。
const OUTPUT_PAIR = "";
// 利用者がスピーカーを手で選んだか。選ぶまではペルソナの選択に合わせて自動で追従させる。
let outputTouched = false;

// ---- 状態チップ ----
async function refreshStatus() {
  try {
    const response = await fetch("/api/status");
    const payload = await response.json();
    for (const key of ["llm", "tts", "stt"]) {
      const state = payload.backend[key];
      const target = els[`status${key.charAt(0).toUpperCase()}${key.slice(1)}`];
      target.textContent = STATUS_LABEL[state] || state;
      document.querySelector(`.chip[data-service="${key}"] .chip__dot`).dataset.state = state;
    }
  } catch (error) {
    setNotice(`状態の取得に失敗しました: ${error.message}`, "error");
  }
}

// ---- ペルソナ選択肢 ----
async function loadDevices() {
  try {
    const response = await fetch("/api/devices");
    const payload = await response.json();
    devices = payload.devices || [];
  } catch (error) {
    setNotice(`デバイス一覧の取得に失敗しました: ${error.message}`, "error");
    devices = [];
  }

  if (devices.length === 0) {
    setNotice("登録済みデバイスがありません。device_profiles.json を確認してください。", "error");
  }

  fillSelect(els.selectA, 0);
  fillSelect(els.selectB, devices.length > 1 ? 1 : 0);
  syncPersonaCards();
}

function fillSelect(select, defaultIndex) {
  select.innerHTML = "";
  devices.forEach((device, index) => {
    const option = document.createElement("option");
    option.value = device.device_id;
    option.textContent = device.display_name || device.device_id;
    if (index === defaultIndex) option.selected = true;
    select.appendChild(option);
  });
}

function findDevice(deviceId) {
  return devices.find((d) => d.device_id === deviceId);
}

function syncPersonaCards() {
  const a = findDevice(els.selectA.value);
  const b = findDevice(els.selectB.value);
  els.nameA.textContent = a ? a.display_name || a.device_id : "—";
  els.idA.textContent = a ? a.device_id : "—";
  if (currentRunMode() === "chat") {
    // 話しかけるモードでは B の位置は利用者自身。
    els.nameB.textContent = "あなた";
    els.idB.textContent = "WEB";
    return;
  }
  els.nameB.textContent = b ? b.display_name || b.device_id : "—";
  els.idB.textContent = b ? b.device_id : "—";
}

// ---- 実機の出力先（スピーカー）----
// relay: 「各ペルソナの実機（2台）」か「この1台で両方」を選ぶ。chat: 答えを鳴らす実機を選ぶ。
// 選択肢は実機 ID で表示する（ペルソナ名で出すと「声の選択」と誤解されるため。声は常にペルソナ側）。
function deviceLabel(id) {
  return `実機 ${id}`;
}

function syncOutputOptions() {
  const mode = currentRunMode();
  const select = els.outputDevice;
  const previous = select.value;
  const options = [];
  if (mode === "relay") {
    options.push({ value: OUTPUT_PAIR, label: "各ペルソナの実機（2台）" });
    for (const id of connectedDevices) {
      options.push({ value: id, label: `1台で両方: ${deviceLabel(id)}` });
    }
  } else if (mode === "chat") {
    for (const id of connectedDevices) options.push({ value: id, label: deviceLabel(id) });
  }

  const signature = JSON.stringify(options);
  if (select.dataset.signature !== signature) {
    select.dataset.signature = signature;
    select.innerHTML = "";
    for (const { value, label } of options) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    }
    const values = options.map((o) => o.value);
    const keep = outputTouched && values.includes(previous);
    select.value = keep ? previous : defaultOutput(mode, values);
  }
  if (mode === "chat" && options.length === 0) {
    select.innerHTML = '<option value="">（接続中の実機なし）</option>';
    select.dataset.signature = "";
  }
}

// ペルソナを変えたとき、スピーカーを手で選んでいなければ既定の出力先へ合わせ直す
// （例: 話し相手を別のペルソナにしたら、そのペルソナの実機がつながっていればそこで鳴らす）。
function followPersonaOutput() {
  syncPersonaCards();
  if (outputTouched || currentRunMode() === "browser") return;
  const values = Array.from(els.outputDevice.options, (o) => o.value);
  els.outputDevice.value = defaultOutput(currentRunMode(), values);
}

// 既定の出力先。relay は両ペルソナの実機がそろっていれば2台、そうでなければ接続中の1台。
// chat はペルソナ自身の実機があればそれ、無ければ接続中の先頭。
function defaultOutput(mode, values) {
  const a = els.selectA.value;
  const b = els.selectB.value;
  if (mode === "relay") {
    if (connectedDevices.includes(a) && connectedDevices.includes(b)) return OUTPUT_PAIR;
    return connectedDevices[0] ?? OUTPUT_PAIR;
  }
  if (values.includes(a)) return a;
  return values[0] ?? "";
}

// ---- 実行モード（画面でデモ／実機で会話／実機に話しかける）----
function currentRunMode() {
  for (const radio of els.runModeRadios) {
    if (radio.checked) return radio.value;
  }
  return "browser";
}

// モードに応じてボタン文言・接続表示・入力欄を切り替える。
function syncModeUI() {
  const mode = currentRunMode();
  const chat = mode === "chat";
  document.body.dataset.runmode = mode;
  if (!running) {
    els.startButton.textContent =
      mode === "relay" ? "実機で会話をはじめる" : chat ? "話しかける" : "デモを再生する";
  }
  els.fieldB.hidden = chat;
  els.fieldTurns.hidden = chat;
  els.fieldOutput.hidden = mode === "browser";
  els.selectALabel.textContent = chat ? "話し相手（ペルソナ）" : "ペルソナ A";
  els.topicLabel.textContent = chat ? "話しかける言葉" : "話題（最初の一言）";
  if (chat && els.topic.value === "好きな食べ物について話しましょう") {
    els.topic.value = "こんにちは。今日はどんな一日だった？";
  }
  updateConnected(connectedDevices);
  syncPersonaCards();
}

async function onSubmit(event) {
  event.preventDefault();
  if (running) return;
  runningMode = currentRunMode();
  if (runningMode === "relay") {
    await startRelay();
  } else if (runningMode === "chat") {
    await sendChat();
  } else {
    await runConversation();
  }
}

// ---- 会話オーケストレーション（ブラウザ模擬） ----
async function runConversation() {
  const deviceA = els.selectA.value;
  const deviceB = els.selectB.value;
  const topic = els.topic.value.trim();
  const maxTurns = clampTurns(parseInt(els.maxTurns.value, 10));

  if (!deviceA || !deviceB) {
    setNotice("ペルソナを2体選んでください。", "error");
    return;
  }
  if (deviceA === deviceB) {
    setNotice("ペルソナ A と B には別のデバイスを選んでください。", "error");
    return;
  }
  if (!topic) {
    setNotice("最初の話題を入力してください。", "error");
    return;
  }

  startRunningUI();
  syncPersonaCards();
  clearLog();
  const sessionId = `web-auto-${Date.now()}`;

  // 各話者の slot（A/B）と device_id・表示名・カード要素をまとめる。
  const speakers = [
    { slot: "a", deviceId: deviceA, name: nameOf(deviceA), card: els.personaA },
    { slot: "b", deviceId: deviceB, name: nameOf(deviceB), card: els.personaB },
  ];

  let current = 0; // 0=A から開始
  let utterance = topic; // 次の話者に渡す直前の発話（初回は話題）

  try {
    for (let turn = 0; turn < maxTurns; turn += 1) {
      if (stopRequested) break;
      const speaker = speakers[current];
      const listener = speakers[1 - current];

      setActiveSpeaker(speaker.slot);
      setBridge(speaker.slot === "a" ? "ab" : "ba", `${speaker.name} が応答中…`);

      const { text, wavBuffer } = await requestTurn(speaker.deviceId, sessionId, utterance);
      if (stopRequested) break;

      appendBubble(speaker, text);
      await playWav(wavBuffer);
      if (stopRequested) break;

      utterance = text;
      current = 1 - current;
      void listener; // 交互の意図を明示（lint 抑止）
    }
  } catch (error) {
    setNotice(describeError(error), "error");
  } finally {
    finishRunningUI();
  }
}

async function requestTurn(deviceId, sessionId, inputText) {
  const response = await fetch("/api/chat/audio", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      device_id: deviceId,
      session_id: sessionId,
      mode: "web",
      input_text: inputText,
      audio: { format: "wav", sample_rate: 16000 },
    }),
  });

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail || body.error?.message || detail;
    } catch (_) {
      /* ボディが JSON でない場合はステータスのみ */
    }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }

  const b64 = response.headers.get("X-LLM-Text-B64");
  const text = b64 ? decodeUtf8Base64(b64) : "(応答テキストなし)";
  const wavBuffer = await response.arrayBuffer();
  return { text, wavBuffer };
}

// ---- 音声再生（再生終了まで await） ----
function playWav(arrayBuffer) {
  return new Promise((resolve, reject) => {
    audioContext ??= new AudioContext();
    if (audioContext.state === "suspended") audioContext.resume();

    audioContext.decodeAudioData(
      arrayBuffer.slice(0),
      (buffer) => {
        if (stopRequested) {
          resolve();
          return;
        }
        if (currentSource) currentSource.stop();
        currentSource = audioContext.createBufferSource();
        currentSource.buffer = buffer;
        currentSource.connect(audioContext.destination);
        currentSource.onended = () => {
          currentSource = null;
          resolve();
        };
        currentSource.start();
      },
      (error) => reject(new Error(`音声のデコードに失敗しました: ${error}`))
    );
  });
}

function stopConversation() {
  if (!running) return;
  if (runningMode === "relay") {
    stopRelay();
    return;
  }
  if (runningMode === "chat") {
    // 再生中の実機を待たずに打ち切る（応答待ちの fetch は停止後に戻る）。
    stopRequested = true;
    void fetch("/api/conversation/stop", { method: "POST" }).catch(() => {});
    setNotice("停止しました。", "ok");
    return;
  }
  stopRequested = true;
  if (currentSource) {
    currentSource.stop();
    currentSource = null;
  }
  setNotice("停止しました。", "ok");
}

// ---- 実機リレー（ホスト統括） ----
async function startRelay() {
  const deviceA = els.selectA.value;
  const deviceB = els.selectB.value;
  const topic = els.topic.value.trim();
  const maxTurns = clampTurns(parseInt(els.maxTurns.value, 10));
  // 空 = 各ペルソナの実機（2台）。device_id = その1台で両ペルソナを鳴らす。
  const output = els.outputDevice.value;

  if (!deviceA || !deviceB) {
    setNotice("ペルソナを2体選んでください。", "error");
    return;
  }
  if (deviceA === deviceB) {
    setNotice("ペルソナ A と B には別のデバイスを選んでください。", "error");
    return;
  }
  const required = output === OUTPUT_PAIR ? [deviceA, deviceB] : [output];
  if (!required.every((id) => connectedDevices.includes(id))) {
    setNotice(
      output === OUTPUT_PAIR
        ? "2台構成では両ペルソナの実機が必要です。実機が1台なら「スピーカー」で1台を選んでください。"
        : "選んだ実機が接続されていません。実機を relay モードにして WiFi 接続を確認してください。",
      "error"
    );
    return;
  }

  startRunningUI();
  syncPersonaCards();
  clearLog();
  setBridge("idle", "会話を準備中…");

  try {
    const response = await fetch("/api/conversation/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        device_a: deviceA,
        device_b: deviceB,
        opening_text: topic || null,
        max_turns: maxTurns,
        output_device_a: output || null,
        output_device_b: output || null,
      }),
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        detail = body.detail || detail;
      } catch (_) {
        /* JSON でなければステータスのみ */
      }
      throw new Error(detail);
    }
    setNotice(
      output === OUTPUT_PAIR
        ? "実機リレーを開始しました。2台のスピーカーから交互に再生されます。"
        : `実機リレーを開始しました。${deviceLabel(output)} が2体の声で交互に話します。`,
      "ok"
    );
    startRelayPolling();
  } catch (error) {
    setNotice(`開始できませんでした: ${error.message}`, "error");
    finishRunningUI();
  }
}

async function stopRelay() {
  stopRelayPolling();
  try {
    await fetch("/api/conversation/stop", { method: "POST" });
  } catch (_) {
    /* 停止要求の失敗は致命的でない */
  }
  setNotice("停止しました。", "ok");
  finishRunningUI();
}

function startRelayPolling() {
  stopRelayPolling();
  relayPollTimer = window.setInterval(async () => {
    try {
      const status = await (await fetch("/api/conversation/status")).json();
      updateConnected(status.connected_devices);
      if (status.running) {
        // ターン番号の偶奇で現在の話者を判定（turn1=A, turn2=B, …）。
        const slot = (status.turn - 1) % 2 === 0 ? "a" : "b";
        setActiveSpeaker(slot);
        setBridge(slot === "a" ? "ab" : "ba", `ターン ${status.turn}/${status.max_turns}`);
      } else {
        stopRelayPolling();
        setNotice("会話が終わりました。", "ok");
        finishRunningUI();
      }
    } catch (_) {
      /* 一時的な取得失敗は次のポーリングで回復 */
    }
  }, 1000);
}

// ---- 実機に話しかける（1 往復チャット）----
async function sendChat() {
  const personaId = els.selectA.value;
  const output = els.outputDevice.value;
  const text = els.topic.value.trim();

  if (!personaId) {
    setNotice("話し相手のペルソナを選んでください。", "error");
    return;
  }
  if (!output || !connectedDevices.includes(output)) {
    setNotice("実機が接続されていません。実機を relay モードにして WiFi 接続を確認してください。", "error");
    return;
  }
  if (!text) {
    setNotice("話しかける言葉を入力してください。", "error");
    return;
  }

  const persona = { slot: "a", name: nameOf(personaId) };
  const me = { slot: "b", name: "あなた" };
  startRunningUI();
  setNotice(`${persona.name} が考えています…`, "ok");
  appendBubble(me, text);
  els.topic.value = "";
  setActiveSpeaker("a");
  setBridge("ba", `${persona.name} が応答中…`);

  try {
    const response = await fetch("/api/conversation/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        persona_id: personaId,
        text,
        output_device: output,
        session_id: chatSessionId,
      }),
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        detail = body.detail || detail;
      } catch (_) {
        /* JSON でなければステータスのみ */
      }
      throw new Error(detail);
    }
    const payload = await response.json();
    appendBubble(persona, payload.text);
    if (!stopRequested) {
      setNotice(`${deviceLabel(payload.output_device)} で ${persona.name} の声で再生しました。`, "ok");
    }
  } catch (error) {
    setNotice(`話しかけられませんでした: ${error.message}`, "error");
  } finally {
    finishRunningUI();
    els.topic.focus();
  }
}

function stopRelayPolling() {
  if (relayPollTimer) {
    window.clearInterval(relayPollTimer);
    relayPollTimer = null;
  }
}

// 接続中デバイスの表示と出力先の選択肢を更新する（実機を使うモードのときだけ表示）。
function updateConnected(list) {
  connectedDevices = list || [];
  const usesDevice = currentRunMode() !== "browser";
  els.connected.hidden = !usesDevice;
  syncOutputOptions();
  if (!usesDevice) return;
  if (connectedDevices.length === 0) {
    els.connected.textContent =
      "接続中のデバイスはありません。実機を relay モードにして WiFi 接続してください。";
    els.connected.dataset.tone = "warn";
  } else {
    els.connected.textContent = `接続中の実機: ${connectedDevices.join(" / ")}`;
    els.connected.dataset.tone = "ok";
  }
}

async function pollConnected() {
  try {
    const status = await (await fetch("/api/conversation/status")).json();
    updateConnected(status.connected_devices);
  } catch (_) {
    /* 取得失敗時は次回に委ねる */
  }
}

// ---- UI ヘルパ ----
function startRunningUI() {
  running = true;
  stopRequested = false;
  els.startButton.disabled = true;
  els.stopButton.disabled = false;
  setNotice("会話を開始します。", "ok");
}

function finishRunningUI() {
  running = false;
  runningMode = null;
  stopRelayPolling();
  els.startButton.disabled = false;
  els.stopButton.disabled = true;
  setActiveSpeaker(null);
  setBridge("idle", stopRequested ? "停止" : "待機中");
  syncModeUI();
}

function setActiveSpeaker(slot) {
  els.personaA.classList.toggle("is-active", slot === "a");
  els.personaB.classList.toggle("is-active", slot === "b");
}

function setBridge(dir, statusText) {
  els.bridge.dataset.dir = dir;
  els.bridgeStatus.textContent = statusText;
}

function appendBubble(speaker, text) {
  const item = document.createElement("li");
  item.className = "bubble";
  item.dataset.voice = speaker.slot;

  const meta = document.createElement("div");
  meta.className = "bubble__meta";
  const name = document.createElement("span");
  name.className = "bubble__name";
  name.textContent = speaker.name;
  const time = document.createElement("span");
  time.className = "bubble__time";
  time.textContent = timestamp();
  meta.append(name, time);

  const body = document.createElement("p");
  body.className = "bubble__text";
  body.textContent = text;

  item.append(meta, body);
  els.logList.appendChild(item);
  els.logEmpty.hidden = true;
  item.scrollIntoView({ behavior: "smooth", block: "end" });
}

function clearLog() {
  els.logList.innerHTML = "";
  els.logEmpty.hidden = false;
}

function setNotice(message, tone) {
  els.notice.textContent = message;
  if (tone) {
    els.notice.dataset.tone = tone;
  } else {
    delete els.notice.dataset.tone;
  }
}

function describeError(error) {
  if (error.status === 503) {
    return `バックエンドが未設定または失敗しています: ${error.message}`;
  }
  if (error.status === 400) {
    return `入力が不正です: ${error.message}`;
  }
  return `会話を中断しました: ${error.message}`;
}

function nameOf(deviceId) {
  const device = findDevice(deviceId);
  return device ? device.display_name || device.device_id : deviceId;
}

function clampTurns(value) {
  if (!Number.isFinite(value)) return 6;
  return Math.min(20, Math.max(2, value));
}

function timestamp() {
  return new Date().toLocaleTimeString("ja-JP", { hour12: false });
}

function decodeUtf8Base64(b64) {
  const bytes = Uint8Array.from(atob(b64), (char) => char.charCodeAt(0));
  return new TextDecoder("utf-8").decode(bytes);
}

// ---- 初期化 ----
els.form.addEventListener("submit", onSubmit);
els.stopButton.addEventListener("click", stopConversation);
els.selectA.addEventListener("change", followPersonaOutput);
els.selectB.addEventListener("change", followPersonaOutput);
els.outputDevice.addEventListener("change", () => {
  outputTouched = true;
});
els.runModeRadios.forEach((radio) =>
  radio.addEventListener("change", () => {
    // モードが変われば出力先の意味も変わるので、手動選択はリセットして既定に戻す。
    outputTouched = false;
    syncModeUI();
  })
);

syncModeUI();
loadDevices();
refreshStatus();
pollConnected();
window.setInterval(refreshStatus, 5000);
window.setInterval(pollConnected, 3000);

// BTicino HOMETOUCH dashboard card: live view, talk, answer a ring, open the gates.
// The microphone goes to the integration as 16-bit PCM at 8 kHz over a
// WebSocket with a signed path; during an answered ring the panel's sound comes
// back on it as 16-bit PCM at 16 kHz. Browsers allow the microphone only over
// HTTPS (or on localhost).

const TEXT = {
  en: {
    talk: "Talk", stop: "Stop talking", answer: "Answer", hangup: "Hang up",
    ringing: "Someone is ringing", answered: "Call answered: talk to the door",
    talking: "Microphone on", https: "The microphone works only when Home Assistant is opened over HTTPS",
    mic: "Microphone not available", confirm: "Open {name}?", error: "Not possible: {error}",
  },
  it: {
    talk: "Parla", stop: "Smetti di parlare", answer: "Rispondi", hangup: "Riaggancia",
    ringing: "Stanno suonando", answered: "Chiamata risposta: parla alla porta",
    talking: "Microfono acceso", https: "Il microfono funziona solo aprendo Home Assistant in HTTPS",
    mic: "Microfono non disponibile", confirm: "Aprire {name}?", error: "Non riuscito: {error}",
  },
};

class BticinoHometouchCard extends HTMLElement {
  static getStubConfig(hass) {
    const camera = Object.keys(hass.states).find((id) => id.startsWith("camera.") && hass.states[id].attributes.talk_url);
    return { entity: camera || "camera.videocitofono_telecamera" };
  }

  setConfig(config) {
    if (!config || !config.entity || !config.entity.startsWith("camera.")) {
      throw new Error("entity: a BTicino HOMETOUCH camera is required");
    }
    this._config = config;
    this._built = false;
  }

  getCardSize() {
    return 7;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    if (this._video) this._video.hass = hass;
    this._renderButtons();
  }

  connectedCallback() {
    this._timer = setInterval(() => this._send({ type: "state" }), 2000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._stopMic();
    if (this._ws) this._ws.close();
    this._ws = null;
  }

  _t(key, values = {}) {
    const lang = (this._hass && this._hass.language || "en").startsWith("it") ? "it" : "en";
    return TEXT[lang][key].replace(/\{(\w+)\}/g, (_, name) => values[name] ?? "");
  }

  async _build() {
    this._built = true;
    this._state = { ringing: false, answered: false };
    const root = this.attachShadow ? (this.shadowRoot || this.attachShadow({ mode: "open" })) : this;
    root.innerHTML = `
      <style>
        ha-card { overflow: hidden; }
        .controls { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px; align-items: center; }
        button { font: inherit; border: none; border-radius: 18px; padding: 8px 16px; cursor: pointer;
                 background: var(--secondary-background-color); color: var(--primary-text-color); }
        button.main { background: var(--primary-color); color: var(--text-primary-color, #fff); }
        button.danger { background: var(--error-color, #db4437); color: #fff; }
        .status { padding: 0 12px 12px; color: var(--secondary-text-color); min-height: 1.2em; }
      </style>
      <ha-card>
        <div class="video"></div>
        <div class="controls"></div>
        <div class="status"></div>
      </ha-card>`;
    this._controls = root.querySelector(".controls");
    this._status = root.querySelector(".status");
    this._videoBox = root.querySelector(".video");
    this._camera = this._config.entity;
    await this._showCamera(this._camera);
    this._renderButtons();
  }

  async _showCamera(entityId) {
    try {
      const helpers = await window.loadCardHelpers();
      const video = await helpers.createCardElement({
        type: "picture-entity", entity: entityId, camera_view: "live",
        show_name: false, show_state: false, tap_action: { action: "more-info" },
      });
      video.hass = this._hass;
      this._videoBox.replaceChildren(video);
      this._video = video;
      this._camera = entityId;
    } catch (err) {
      this._setStatus(String(err));
    }
  }

  // The entrance panel's cameras: the configured one first, then the others of the same device.
  _cameras() {
    const hass = this._hass;
    const main = this._config.entity;
    const camera = hass.entities && hass.entities[main];
    const others = camera && camera.device_id ? Object.values(hass.entities)
      .filter((e) => e.device_id === camera.device_id && e.entity_id.startsWith("camera.") && e.entity_id !== main)
      .map((e) => e.entity_id).sort() : [];
    return [main, ...others];
  }

  async _nextCamera() {
    const cameras = this._cameras();
    const next = cameras[(cameras.indexOf(this._camera) + 1) % cameras.length];
    this._signature = null;
    await this._showCamera(next);
    this._renderButtons();
  }

  _openButtons() {
    if (this._config.buttons) return this._config.buttons;
    const hass = this._hass;
    const camera = hass.entities && hass.entities[this._config.entity];
    if (!camera || !camera.device_id) return [];
    return Object.values(hass.entities)
      .filter((e) => e.device_id === camera.device_id && e.entity_id.startsWith("button."))
      .map((e) => e.entity_id).sort();
  }

  _renderButtons() {
    if (!this._controls || !this._hass) return;
    const items = [];
    const { ringing, answered } = this._state;
    if (answered) {
      items.push([this._talking ? this._t("stop") : this._t("talk"), "main", () => this._toggleMic()]);
      items.push([this._t("hangup"), "danger", () => this._hangup()]);
    } else if (ringing) {
      items.push([this._t("answer"), "main", () => this._answer()]);
    } else {
      items.push([this._talking ? this._t("stop") : this._t("talk"), "main", () => this._toggleMic()]);
    }
    const cameras = this._cameras();
    if (cameras.length > 1 && !answered && !ringing) {
      const next = cameras[(cameras.indexOf(this._camera) + 1) % cameras.length];
      const state = this._hass.states[next];
      const label = state ? (state.attributes.friendly_name || next).replace(/^Videocitofono\s+/, "") : next;
      items.push([`▶ ${label}`, "", () => this._nextCamera()]);
    }
    for (const entityId of this._openButtons()) {
      const state = this._hass.states[entityId];
      const name = state ? (state.attributes.friendly_name || entityId).replace(/^.*?\s(?=Apri|Open)/, "") : entityId;
      items.push([name, "", () => this._open(entityId, name)]);
    }
    const signature = JSON.stringify(items.map(([label, kind]) => [label, kind]));
    if (signature === this._signature) return;
    this._signature = signature;
    this._controls.replaceChildren(...items.map(([label, kind, action]) => {
      const button = document.createElement("button");
      button.textContent = label;
      if (kind) button.className = kind;
      button.addEventListener("click", action);
      return button;
    }));
  }

  _setStatus(text) {
    if (this._status) this._status.textContent = text || "";
  }

  async _connect() {
    if (this._ws && this._ws.readyState <= 1) return this._ws;
    const camera = this._hass.states[this._config.entity];
    const path = camera && camera.attributes.talk_url;
    if (!path) throw new Error("talk_url");
    const signed = await this._hass.callWS({ type: "auth/sign_path", path, expires: 3600 });
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${signed.path}`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = (event) => this._message(event);
    ws.onclose = () => {
      if (this._ws === ws) this._ws = null;
      this._stopMic();
      this._state = { ringing: false, answered: false };
      this._renderButtons();
    };
    this._ws = ws;
    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = () => reject(new Error("WebSocket"));
    });
    return ws;
  }

  async _send(message) {
    try {
      if (message.type === "state" && !this._ws) await this._connect();
      if (this._ws && this._ws.readyState === 1) this._ws.send(JSON.stringify(message));
    } catch (err) {
      /* the next poll retries */
    }
  }

  _message(event) {
    if (typeof event.data !== "string") {
      this._play(event.data);
      return;
    }
    const message = JSON.parse(event.data);
    if (message.type === "state") {
      this._state = { ringing: message.ringing, answered: message.answered };
      if (!this._talking) this._setStatus(message.answered ? this._t("answered") : message.ringing ? this._t("ringing") : "");
    } else if (message.type === "answered") {
      this._state = { ringing: false, answered: true };
      this._setStatus(this._t("answered"));
    } else if (message.type === "error") {
      this._setStatus(this._t("error", { error: message.error }));
    }
    this._renderButtons();
  }

  async _answer() {
    try {
      await this._connect();
      await this._startMic();
      this._ws.send(JSON.stringify({ type: "answer" }));
    } catch (err) {
      this._setStatus(this._t("error", { error: err.message }));
    }
  }

  async _hangup() {
    this._stopMic();
    this._send({ type: "hangup" });
  }

  async _toggleMic() {
    if (this._talking) {
      this._stopMic();
      this._renderButtons();
      return;
    }
    try {
      await this._connect();
      await this._startMic();
    } catch (err) {
      this._setStatus(err.message);
    }
    this._renderButtons();
  }

  _audioContext() {
    if (!this._ctx || this._ctx.state === "closed") this._ctx = new AudioContext();
    if (this._ctx.state === "suspended") this._ctx.resume();
    return this._ctx;
  }

  async _startMic() {
    if (this._talking) return;
    if (!window.isSecureContext || !navigator.mediaDevices) throw new Error(this._t("https"));
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (err) {
      throw new Error(this._t("mic"));
    }
    const ctx = this._audioContext();
    const source = ctx.createMediaStreamSource(stream);
    const processor = ctx.createScriptProcessor(2048, 1, 1);
    const mute = ctx.createGain();
    mute.gain.value = 0;
    const ratio = ctx.sampleRate / 8000;
    let carry = 0;
    processor.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0);
      const count = Math.floor((input.length - carry) / ratio);
      const pcm = new Int16Array(count);
      for (let i = 0; i < count; i++) {
        const from = Math.floor(carry + i * ratio);
        const to = Math.min(input.length, Math.floor(carry + (i + 1) * ratio));
        let sum = 0;
        for (let j = from; j < to; j++) sum += input[j];
        const value = sum / Math.max(1, to - from);
        pcm[i] = Math.max(-32768, Math.min(32767, Math.round(value * 32767)));
      }
      carry = carry + count * ratio - input.length;
      if (carry < 0) carry = 0;
      if (this._ws && this._ws.readyState === 1) this._ws.send(pcm.buffer);
    };
    source.connect(processor);
    processor.connect(mute);
    mute.connect(ctx.destination);
    this._mic = { stream, source, processor, mute };
    this._talking = true;
    this._setStatus(this._t("talking"));
  }

  _stopMic() {
    if (this._mic) {
      this._mic.processor.onaudioprocess = null;
      this._mic.source.disconnect();
      this._mic.processor.disconnect();
      this._mic.mute.disconnect();
      this._mic.stream.getTracks().forEach((track) => track.stop());
    }
    this._mic = null;
    this._talking = false;
  }

  _play(buffer) {
    const ctx = this._audioContext();
    const samples = new Int16Array(buffer);
    if (!samples.length) return;
    const audio = ctx.createBuffer(1, samples.length, 16000);
    const channel = audio.getChannelData(0);
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;
    const node = ctx.createBufferSource();
    node.buffer = audio;
    node.connect(ctx.destination);
    // A small lead absorbs network jitter; fall back to "now" after a gap.
    const start = Math.max(ctx.currentTime + 0.08, this._next || 0);
    this._next = start > ctx.currentTime + 0.5 ? ctx.currentTime + 0.08 : start;
    node.start(this._next);
    this._next += audio.duration;
  }

  async _open(entityId, name) {
    if (!window.confirm(this._t("confirm", { name }))) return;
    try {
      await this._hass.callService("button", "press", { entity_id: entityId });
    } catch (err) {
      this._setStatus(this._t("error", { error: err.message }));
    }
  }
}

customElements.define("bticino-hometouch-card", BticinoHometouchCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "bticino-hometouch-card",
  name: "BTicino HOMETOUCH",
  description: "Live view, talk, answer a ring and open the gates",
});

/* Energy Wallet card: purchase cost vs. sale revenue for grid energy. */

const VERSION = "0.1.1";

const I18N = {
  en: {
    title: "Energy wallet",
    today: "Today",
    month: "Month",
    year: "Year",
    bought: "Bought",
    sold: "Sold",
    balance: "Balance",
    buyNow: "Buy now",
    sellNow: "Sell now",
    fixed: "Fixed fees",
    perMonth: "/ month",
    unpriced: "waiting for price",
    estimate: "RCEm estimate",
    prices: "Prices",
    noEntities: "No Energy Wallet sensors found. Add the integration first.",
  },
  pl: {
    title: "Portfel energii",
    today: "Dziś",
    month: "Miesiąc",
    year: "Rok",
    bought: "Kupiono",
    sold: "Sprzedano",
    balance: "Bilans",
    buyNow: "Zakup teraz",
    sellNow: "Sprzedaż teraz",
    fixed: "Opłaty stałe",
    perMonth: "/ mies.",
    unpriced: "czeka na cenę",
    estimate: "szacunek RCEm",
    prices: "Ceny",
    noEntities: "Nie znaleziono sensorów Energy Wallet. Najpierw dodaj integrację.",
  },
};

const PERIODS = ["today", "month", "year"];

class EnergyWalletCard extends HTMLElement {
  static getStubConfig() {
    return { period: "today", show_chart: true };
  }

  setConfig(config) {
    this._config = { period: "today", show_chart: true, ...config };
    this._period = PERIODS.includes(this._config.period) ? this._config.period : "today";
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const signature = this._signature();
    if (signature !== this._lastSignature) {
      this._lastSignature = signature;
      this._render();
    }
  }

  getCardSize() {
    return this._config && this._config.show_chart ? 6 : 4;
  }

  _t(key) {
    const lang = (this._hass && (this._hass.locale?.language || this._hass.language)) || "en";
    return (I18N[lang.split("-")[0]] || I18N.en)[key];
  }

  _entities() {
    const found = {};
    if (!this._hass) return found;
    for (const [entityId, state] of Object.entries(this._hass.states)) {
      const meta = state.attributes && state.attributes.energy_wallet;
      if (!meta) continue;
      if (this._config.entry_id && meta.entry_id !== this._config.entry_id) continue;
      found[`${meta.role}:${meta.period ?? ""}`] = entityId;
    }
    return found;
  }

  _signature() {
    const ids = Object.values(this._entities());
    return ids.map((id) => {
      const s = this._hass.states[id];
      return `${id}=${s.state}@${s.last_updated}`;
    }).join("|") + `|${this._period}`;
  }

  _state(key) {
    const id = this._entities()[key];
    return id ? this._hass.states[id] : undefined;
  }

  _num(state) {
    const n = state ? parseFloat(state.state) : NaN;
    return Number.isFinite(n) ? n : null;
  }

  _money(value) {
    if (value === null) return "—";
    const lang = this._hass?.locale?.language || "pl";
    const digits = Math.abs(value) >= 100 ? 0 : 2;
    return new Intl.NumberFormat(lang, {
      style: "currency", currency: "PLN", minimumFractionDigits: digits, maximumFractionDigits: digits,
    }).format(value);
  }

  _kwh(value) {
    if (value === null || value === undefined) return "—";
    const lang = this._hass?.locale?.language || "pl";
    return `${new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format(value)} kWh`;
  }

  _price(value) {
    if (value === null) return "—";
    const lang = this._hass?.locale?.language || "pl";
    return `${new Intl.NumberFormat(lang, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value)} zł/kWh`;
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  _tile(cls, label, key, energyText, note) {
    const state = this._state(key);
    const id = this._entities()[key] || "";
    return `
      <button class="tile ${cls}" data-entity="${id}">
        <span class="label">${label}</span>
        <span class="value">${this._money(this._num(state))}</span>
        <span class="sub">${energyText ?? "&nbsp;"}</span>
        ${note ? `<span class="note">${note}</span>` : ""}
      </button>`;
  }

  _chart() {
    const forecast = this._state("buy_price:")?.attributes?.forecast;
    if (!this._config.show_chart || !Array.isArray(forecast) || forecast.length < 2) return "";
    const W = 300, H = 90, P = 4;
    const t0 = Date.parse(forecast[0].start);
    const t1 = Date.parse(forecast[forecast.length - 1].end);
    const values = forecast.flatMap((p) => [p.buy, p.sell]).filter((v) => v !== null);
    const min = Math.min(0, ...values);
    const max = Math.max(...values, 0.01);
    const x = (t) => P + ((t - t0) / (t1 - t0)) * (W - 2 * P);
    const y = (v) => H - P - ((v - min) / (max - min)) * (H - 2 * P);
    // Continuous step line; a missing price starts a new segment.
    const step = (key) => {
      let d = "";
      let prevEnd = null;
      for (const p of forecast) {
        if (p[key] === null) { prevEnd = null; continue; }
        const xs = x(Date.parse(p.start)).toFixed(1);
        const yv = y(p[key]).toFixed(1);
        d += prevEnd === p.start ? `V${yv}` : `M${xs},${yv}`;
        d += `H${x(Date.parse(p.end)).toFixed(1)}`;
        prevEnd = p.end;
      }
      return d;
    };
    const now = x(Date.now());
    const zero = min < 0 ? `<line class="zero" x1="${P}" x2="${W - P}" y1="${y(0)}" y2="${y(0)}"/>` : "";
    return `
      <div class="chart">
        <div class="legend">
          <span><i class="buy"></i>${this._t("buyNow").split(" ")[0]}</span>
          <span><i class="sell"></i>${this._t("sellNow").split(" ")[0]}</span>
          <span class="range">${this._price(min)} – ${this._price(max)}</span>
        </div>
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${this._t("prices")}">
          ${zero}
          <path class="buy" d="${step("buy")}"/>
          <path class="sell" d="${step("sell")}"/>
          <line class="now" x1="${now}" x2="${now}" y1="0" y2="${H}"/>
        </svg>
      </div>`;
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    if (!this._hass) {
      this.shadowRoot.innerHTML = "";
      return;
    }
    const entities = this._entities();
    const p = this._period;
    let body;
    if (!Object.keys(entities).length) {
      body = `<div class="empty">${this._t("noEntities")}</div>`;
    } else {
      const cost = this._state(`purchase_cost:${p}`);
      const rev = this._state(`sale_revenue:${p}`);
      const bal = this._num(this._state(`balance:${p}`));
      const unpriced = (s) => (s?.attributes?.unpriced_kwh > 0 ? `${this._kwh(s.attributes.unpriced_kwh)} ${this._t("unpriced")}` : "");
      const fixed = this._state(`balance:${p}`)?.attributes?.fixed_monthly_gross;
      const rcem = rev?.attributes?.sale_mode === "rcem" ? this._t("estimate") : "";
      body = `
        <div class="tabs" role="tablist">
          ${PERIODS.map((k) => `<button role="tab" class="tab ${k === p ? "active" : ""}" data-period="${k}" aria-selected="${k === p}">${this._t(k)}</button>`).join("")}
        </div>
        <div class="tiles">
          ${this._tile("cost", this._t("bought"), `purchase_cost:${p}`, this._kwh(cost?.attributes?.energy_kwh), unpriced(cost))}
          ${this._tile("rev", this._t("sold"), `sale_revenue:${p}`, this._kwh(rev?.attributes?.energy_kwh), [unpriced(rev), rcem].filter(Boolean).join(" · "))}
          ${this._tile(`bal ${bal === null ? "" : bal >= 0 ? "pos" : "neg"}`, this._t("balance"), `balance:${p}`, fixed ? `${this._t("fixed")}: ${this._money(fixed)} ${this._t("perMonth")}` : "")}
        </div>
        <div class="now-prices">
          <button class="price" data-entity="${entities["buy_price:"] || ""}"><span>${this._t("buyNow")}</span><b>${this._price(this._num(this._state("buy_price:")))}</b></button>
          <button class="price" data-entity="${entities["sell_price:"] || ""}"><span>${this._t("sellNow")}</span><b>${this._price(this._num(this._state("sell_price:")))}</b></button>
        </div>
        ${this._chart()}`;
    }
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="header">${this._config.title ?? this._t("title")}</div>
        <div class="content">${body}</div>
      </ha-card>`;
    this.shadowRoot.querySelectorAll(".tab").forEach((el) =>
      el.addEventListener("click", () => {
        this._period = el.dataset.period;
        this._lastSignature = null;
        this._render();
      })
    );
    this.shadowRoot.querySelectorAll("[data-entity]").forEach((el) =>
      el.addEventListener("click", () => this._moreInfo(el.dataset.entity))
    );
  }
}

const STYLE = `
  :host { --ew-buy: var(--error-color, #db4437); --ew-sell: var(--success-color, #43a047); }
  .header { padding: 16px 16px 0; font-size: 1.15em; font-weight: 500; color: var(--primary-text-color); }
  .content { padding: 12px 16px 16px; display: grid; gap: 12px; color: var(--primary-text-color); }
  button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; text-align: left; }
  .tabs { display: flex; gap: 4px; background: var(--secondary-background-color); border-radius: 10px; padding: 3px; }
  .tab { flex: 1; text-align: center; padding: 6px 0; border-radius: 8px; color: var(--secondary-text-color); font-size: 0.9em; }
  .tab.active { background: var(--card-background-color, #fff); color: var(--primary-text-color); font-weight: 500; box-shadow: 0 1px 2px rgba(0,0,0,.12); }
  .tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
  .tile { display: grid; align-content: start; gap: 2px; padding: 10px; border-radius: 12px; background: var(--secondary-background-color); min-width: 0; }
  .tile .label { font-size: 0.8em; color: var(--secondary-text-color); }
  .tile .value { font-size: 1.2em; font-weight: 600; color: var(--primary-text-color); white-space: nowrap; }
  .tile .sub, .tile .note { font-size: 0.75em; color: var(--secondary-text-color); }
  .tile .note { font-style: italic; }
  .tile.cost .value { color: var(--ew-buy); }
  .tile.rev .value { color: var(--ew-sell); }
  .tile.bal.pos .value { color: var(--ew-sell); }
  .tile.bal.neg .value { color: var(--ew-buy); }
  .now-prices { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .price { display: grid; gap: 2px; padding: 8px 10px; border: 1px solid var(--divider-color); border-radius: 10px; }
  .price span { color: var(--secondary-text-color); font-size: 0.8em; }
  .price b { font-weight: 600; }
  .chart .legend { display: flex; gap: 12px; font-size: 0.75em; color: var(--secondary-text-color); margin-bottom: 4px; }
  .chart .legend i { display: inline-block; width: 10px; height: 3px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }
  .chart .legend i.buy { background: var(--ew-buy); }
  .chart .legend i.sell { background: var(--ew-sell); }
  .chart .legend .range { margin-left: auto; }
  .chart svg { width: 100%; height: 90px; display: block; }
  .chart path { fill: none; stroke-width: 2; vector-effect: non-scaling-stroke; }
  .chart path.buy { stroke: var(--ew-buy); }
  .chart path.sell { stroke: var(--ew-sell); }
  .chart .now { stroke: var(--primary-text-color); stroke-width: 1; stroke-dasharray: 2 3; opacity: .5; vector-effect: non-scaling-stroke; }
  .chart .zero { stroke: var(--divider-color); stroke-width: 1; vector-effect: non-scaling-stroke; }
  .empty { color: var(--secondary-text-color); }
  @media (max-width: 420px) { .tiles { grid-template-columns: 1fr; } }
`;

if (!customElements.get("energy-wallet-card")) {
  customElements.define("energy-wallet-card", EnergyWalletCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "energy-wallet-card",
    name: "Energy Wallet",
    description: "Purchase cost vs. sale revenue of grid energy",
    preview: true,
  });
  console.info(`%c ENERGY-WALLET-CARD %c ${VERSION} `, "background:#43a047;color:#fff", "background:#333;color:#fff");
}

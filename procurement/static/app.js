/* Дашборд закупщика: вкладки, сортировка таблиц, «Все SKU» с фильтрами, карточка SKU, графики.
   Данные — window.PROCUREMENT_DATA из data/data.js (работает и с file://, и на GitHub Pages). */
(function () {
  "use strict";
  var D = window.PROCUREMENT_DATA || {};
  var L = D.labels || {};
  var items = D.items || [];
  var byId = {};
  items.forEach(function (r) { byId[r.sku_id] = r; });
  var cfg = D.config || {};
  var DEAD = { dead_sell: 1, dead_close: 1, no_demand: 1 };
  var COLORS = { accent: "#1f5fbf", actual: "#9db3cf", forecast: "#1f5fbf", stock: "#2f8f5b", transit: "#d08a1f",
                 order: "#7a4fd0", safety: "#c2410c", naive: "#9aa3b0", grid: "#eef1f5", axis: "#8b95a4" };

  // --- форматирование ----------------------------------------------------------------------
  var nf0 = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
  var nf1 = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
  function n0(v) { return v === null || v === undefined || v === "" ? "—" : nf0.format(v).replace("-", "−"); }
  function n1(v) { return v === null || v === undefined ? "—" : nf1.format(v).replace("-", "−"); }
  function fdate(s) { if (!s) return "—"; var p = String(s).slice(0, 10).split("-"); return p[2] + "." + p[1] + "." + p[0]; }
  var MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  function fmonth(m) { var p = String(m).split("-"); return MONTHS[+p[1] - 1] + " " + p[0].slice(2); }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function catLabel(c) { return (L.category || {})[c] || c || "—"; }
  function statusBadge(s) { return '<span class="badge st-' + esc(s) + '">' + esc((L.status || {})[s] || s) + "</span>"; }
  function riskBadges(rs) {
    return (rs || []).map(function (r) { return '<span class="badge rk-' + esc(r) + '">' + esc((L.risk || {})[r] || r) + "</span>"; }).join("");
  }
  var HOT = { "скоро кончится": "flag-hot", "нет себестоимости": "flag-warn", "низкая наценка": "flag-warn" };
  function flagBadges(fs) {
    return (fs || []).map(function (f) { return '<span class="flag ' + (HOT[f] || "") + '">' + esc(f) + "</span>"; }).join("");
  }
  function coverText(r) {
    if (r.cover_days !== null && r.cover_days !== undefined) return n0(r.cover_days);
    return DEAD[r.status] ? "—" : "> " + (cfg.forecast_months || 18) + " мес";
  }
  function pctTrend(t) { if (t === null || t === undefined) return "—"; var v = Math.round((t - 1) * 100); return (v > 0 ? "+" : "") + n0(v) + "%"; }

  // --- вкладки ------------------------------------------------------------------------------
  var panels = {};
  document.querySelectorAll("[data-panel]").forEach(function (p) { panels[p.getAttribute("data-panel")] = p; });
  var charts = [];
  var inited = {};
  function showTab(name, anchor) {
    if (!panels[name]) name = "summary";
    Object.keys(panels).forEach(function (k) { panels[k].hidden = k !== name; });
    document.querySelectorAll(".tab").forEach(function (t) { t.classList.toggle("active", t.getAttribute("data-tab") === name); });
    if (!inited[name]) { inited[name] = true; (INIT[name] || function () {})(); }
    charts.forEach(function (c) { c.resize(); });
    if (anchor) {
      var el = document.getElementById(anchor);
      if (el) { if (el.tagName === "DETAILS") el.open = true; el.scrollIntoView({ block: "start" }); }
    } else { window.scrollTo(0, 0); }
  }
  function route() {
    var h = decodeURIComponent((location.hash || "#summary").slice(1));
    if (h.indexOf("sku=") === 0) { openSku(h.slice(4)); if (!document.querySelector(".tab.active")) showTab("summary"); return; }
    closeSku(true);
    var el = document.getElementById(h);
    if (panels[h]) return showTab(h);
    if (el) {
      var p = el.closest("[data-panel]");
      if (p) return showTab(p.getAttribute("data-panel"), h);
    }
    showTab("summary");
  }
  window.addEventListener("hashchange", route);
  window.addEventListener("resize", function () { charts.forEach(function (c) { c.resize(); }); });

  // --- сортировка серверных таблиц ----------------------------------------------------------
  function cellValue(td, type) {
    var v = td.hasAttribute("data-v") ? td.getAttribute("data-v") : td.textContent.trim();
    if (type === "num") { var x = parseFloat(String(v).replace(/\s/g, "").replace(",", ".").replace("−", "-")); return isNaN(x) ? -Infinity : x; }
    return String(v).toLowerCase();
  }
  document.addEventListener("click", function (e) {
    var th = e.target.closest("table.sortable th");
    if (th && !th.classList.contains("nosort")) {
      var table = th.closest("table"), idx = Array.prototype.indexOf.call(th.parentNode.children, th);
      var type = th.getAttribute("data-type") || "text";
      var asc = !th.classList.contains("sorted-asc");
      if (!th.classList.contains("sorted-asc") && !th.classList.contains("sorted-desc") && type === "num") asc = false;
      table.querySelectorAll("th").forEach(function (h) { h.classList.remove("sorted-asc", "sorted-desc"); });
      th.classList.add(asc ? "sorted-asc" : "sorted-desc");
      var body = table.tBodies[0], rows = Array.prototype.slice.call(body.rows);
      rows.sort(function (a, b) {
        var x = cellValue(a.cells[idx], type), y = cellValue(b.cells[idx], type);
        return (x < y ? -1 : x > y ? 1 : 0) * (asc ? 1 : -1);
      });
      rows.forEach(function (r) { body.appendChild(r); });
      return;
    }
    var close = e.target.closest("[data-close]");
    if (close) { closeSku(); return; }
    if (e.target.closest("a, summary, button, select, input")) return;
    var row = e.target.closest("[data-sku]");
    if (row) { location.hash = "sku=" + row.getAttribute("data-sku"); }
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeSku(); });

  // --- ECharts ------------------------------------------------------------------------------
  function mkChart(el) {
    if (!window.echarts || !el) return null;
    var c = window.echarts.init(el, null, { renderer: "canvas" });
    charts.push(c);
    return c;
  }
  var baseText = { fontFamily: getComputedStyle(document.body).fontFamily, color: "#5f6b7b", fontSize: 11 };
  function axisCommon() {
    return { axisLine: { lineStyle: { color: "#d6dbe2" } }, axisTick: { show: false }, axisLabel: { color: COLORS.axis, fontSize: 11 },
             splitLine: { lineStyle: { color: COLORS.grid } } };
  }
  function tooltipFmt(params) {
    var head = params.length ? esc(params[0].axisValueLabel) : "";
    return head + params.filter(function (p) { return p.value !== null && p.value !== undefined && p.value !== "-"; }).map(function (p) {
      var v = Array.isArray(p.value) ? p.value[1] : p.value;
      return "<br>" + p.marker + esc(p.seriesName) + ": <b>" + n0(v) + "</b>";
    }).join("");
  }

  function initCalendar() {
    var cal = D.calendar || [];
    var c = mkChart(document.getElementById("chart-calendar"));
    if (!c) return;
    c.setOption({
      textStyle: baseText, animation: false,
      grid: { left: 52, right: 44, top: 26, bottom: 46 },
      legend: { bottom: 0, left: 0, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11, color: "#5f6b7b" } },
      tooltip: { trigger: "axis", formatter: tooltipFmt },
      xAxis: Object.assign({ type: "category", data: cal.map(function (m) { return fmonth(m.month); }) }, axisCommon(), { splitLine: { show: false } }),
      yAxis: [Object.assign({ type: "value", name: "шт.", nameTextStyle: { color: COLORS.axis, fontSize: 11 } }, axisCommon(), { axisLabel: { color: COLORS.axis, fontSize: 11, formatter: function (v) { return n0(v); } } }),
              Object.assign({ type: "value", name: "поз.", nameTextStyle: { color: COLORS.axis, fontSize: 11 } }, axisCommon(), { splitLine: { show: false } })],
      series: [
        { name: "Штук к заказу", type: "bar", barMaxWidth: 34, data: cal.map(function (m, i) { return { value: m.qty, itemStyle: { color: i === 0 ? COLORS.accent : "#a9c1e8", borderRadius: [3, 3, 0, 0] } }; }) },
        { name: "Позиций", type: "line", yAxisIndex: 1, data: cal.map(function (m) { return m.positions; }), symbolSize: 6, lineStyle: { color: "#5f6b7b", width: 1.5 }, itemStyle: { color: "#5f6b7b" } }
      ]
    });
  }

  function initAccuracy() {
    var series = (D.backtest || {}).series || {};
    document.querySelectorAll("[data-acc]").forEach(function (el) {
      var rows = series[el.getAttribute("data-acc")] || [];
      var c = mkChart(el);
      if (!c) return;
      c.setOption({
        textStyle: baseText, animation: false,
        grid: { left: 46, right: 10, top: 26, bottom: 22 },
        legend: { top: 0, left: 0, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11, color: "#5f6b7b" } },
        tooltip: { trigger: "axis", formatter: tooltipFmt },
        xAxis: Object.assign({ type: "category", data: rows.map(function (r) { return fmonth(r.month); }) }, axisCommon(), { splitLine: { show: false } }),
        yAxis: Object.assign({ type: "value" }, axisCommon(), { axisLabel: { color: COLORS.axis, fontSize: 10, formatter: function (v) { return n0(v); } } }),
        series: [
          { name: "Факт", type: "bar", barMaxWidth: 16, data: rows.map(function (r) { return r.actual; }), itemStyle: { color: "#c9d6e8", borderRadius: [2, 2, 0, 0] } },
          { name: "Модель", type: "line", data: rows.map(function (r) { return r.model; }), symbol: "circle", symbolSize: 4, lineStyle: { color: COLORS.forecast, width: 2 }, itemStyle: { color: COLORS.forecast } },
          { name: "Наивный", type: "line", data: rows.map(function (r) { return r.naive; }), symbol: "none", lineStyle: { color: COLORS.naive, width: 1.5, type: "dashed" }, itemStyle: { color: COLORS.naive } }
        ]
      });
    });
  }

  // --- «Все SKU» ----------------------------------------------------------------------------
  var state = { q: "", category: "", status: "", supplier: "", abc: "", risk: "", key: "status", dir: 1 };
  var STATUS_ORDER = { order_now: 0, order_later: 1, review_price: 2, ok: 3, dead_sell: 4, dead_close: 5, no_demand: 6 };
  function sortVal(r, key) {
    if (key === "status") return STATUS_ORDER[r.status] !== undefined ? STATUS_ORDER[r.status] : 9;
    if (key === "risk") return (r.risk || []).length * 10 + (r.flags || []).length;
    if (key === "cover_days") return r.cover_days === null ? (DEAD[r.status] ? -1 : 1e9) : r.cover_days;
    var v = r[key];
    if (v === null || v === undefined) return null;
    return typeof v === "string" ? v.toLowerCase() : v;
  }
  function initSkus() {
    var sel = document.getElementById("f-supplier");
    var sups = {};
    items.forEach(function (r) { sups[r.supplier_id || ""] = 1; });
    Object.keys(sups).sort().forEach(function (s) {
      var o = document.createElement("option");
      o.value = s || "__none"; o.textContent = s || "не заведён";
      sel.appendChild(o);
    });
    ["q", "category", "status", "supplier", "abc", "risk"].forEach(function (k) {
      var el = document.getElementById("f-" + k);
      el.addEventListener(k === "q" ? "input" : "change", function () { state[k] = el.value.trim(); renderSkus(); });
    });
    document.getElementById("f-reset").addEventListener("click", function () {
      ["q", "category", "status", "supplier", "abc", "risk"].forEach(function (k) { document.getElementById("f-" + k).value = ""; state[k] = ""; });
      renderSkus();
    });
    document.querySelectorAll("#sku-table th[data-key]").forEach(function (th) {
      th.addEventListener("click", function () {
        var k = th.getAttribute("data-key");
        if (state.key === k) state.dir = -state.dir;
        else { state.key = k; state.dir = th.classList.contains("n") ? -1 : 1; }
        renderSkus();
      });
    });
    renderSkus();
  }
  function renderSkus() {
    var q = state.q.toLowerCase();
    var rows = items.filter(function (r) {
      if (q && (r.sku_id.toLowerCase().indexOf(q) < 0) && String(r.supplier_id || "").toLowerCase().indexOf(q) < 0) return false;
      if (state.category && r.category !== state.category) return false;
      if (state.status && r.status !== state.status) return false;
      if (state.supplier && (r.supplier_id || "__none") !== state.supplier) return false;
      if (state.abc && r.abc !== state.abc) return false;
      if (state.risk === "__any" && !(r.risk || []).length) return false;
      if (state.risk && state.risk !== "__any" && (r.risk || []).indexOf(state.risk) < 0) return false;
      return true;
    });
    var k = state.key, dir = state.dir;
    rows.sort(function (a, b) {
      var x = sortVal(a, k), y = sortVal(b, k);
      if (x === y) { var oa = b.order_qty - a.order_qty; return oa || (a.sku_id < b.sku_id ? -1 : 1); }
      if (x === null) return 1;
      if (y === null) return -1;
      return (x < y ? -1 : 1) * dir;
    });
    document.querySelectorAll("#sku-table th[data-key]").forEach(function (th) {
      th.classList.remove("sorted-asc", "sorted-desc");
      if (th.getAttribute("data-key") === k) th.classList.add(dir > 0 ? "sorted-asc" : "sorted-desc");
    });
    var html = rows.map(function (r) {
      var lead = '<span title="' + esc((L.lead_source || {})[r.lead_source] || "") + '">' + n0(r.lead_days) + "</span>" +
        ((r.flags || []).indexOf("плечо оценочное") >= 0 ? '<span class="lead-src">оценочное</span>' : "");
      var fl = (r.flags || []).filter(function (f) { return f !== "нет продаж 12 мес" && f !== "плечо оценочное"; });
      var hot = fl.filter(function (f) { return HOT[f]; }), rest = fl.filter(function (f) { return !HOT[f]; });
      var tags = riskBadges(r.risk) + flagBadges(hot) +
        (rest.length ? '<span class="flag" title="' + esc(rest.join(", ")) + '">' + (rest.length === 1 ? esc(rest[0]) : "флаги: " + rest.length) + "</span>" : "");
      return '<tr data-sku="' + esc(r.sku_id) + '">' +
        '<td class="sku"><span class="sku-link">' + esc(r.sku_id) + "</span></td>" +
        "<td>" + esc(catLabel(r.category)) + "</td>" +
        "<td>" + esc(r.supplier_id || "не заведён") + "</td>" +
        "<td>" + esc(r.abc || "") + "</td>" +
        "<td>" + statusBadge(r.status) + "</td>" +
        '<td class="n ' + (r.order_qty ? "strong" : "muted") + '">' + (r.order_qty ? n0(r.order_qty) : "—") + "</td>" +
        "<td>" + fdate(r.order_by) + "</td>" +
        '<td class="n">' + n0(r.stock_now) + "</td>" +
        '<td class="n">' + (r.in_transit_qty ? n0(r.in_transit_qty) : '<span class="muted">0</span>') + "</td>" +
        "<td>" + fdate(r.stockout_date) + "</td>" +
        '<td class="n">' + coverText(r) + "</td>" +
        '<td class="n">' + lead + "</td>" +
        '<td class="n">' + n0(r.sales_12m) + "</td>" +
        '<td class="n">' + n0(r.orders_12m) + "</td>" +
        '<td class="n">' + n0(r.rank_in_category) + "</td>" +
        '<td class="n ' + (r.trend > 1 ? "txt-green" : r.trend < 1 ? "txt-red" : "muted") + '">' + pctTrend(r.trend) + "</td>" +
        '<td class="n' + (r.markup_12m !== null && r.markup_12m < (cfg.min_markup_pct || 25) ? " txt-red" : "") + '">' + (r.markup_12m === null ? '<span class="muted">—</span>' : n0(r.markup_12m) + "%") + "</td>" +
        '<td class="tags">' + tags + "</td></tr>";
    }).join("");
    document.querySelector("#sku-table tbody").innerHTML = html || '<tr><td colspan="18" class="muted">Ничего не найдено — измените фильтры.</td></tr>';
    var qty = rows.reduce(function (s, r) { return s + (r.status === "order_now" ? r.order_qty : 0); }, 0);
    document.getElementById("f-count").textContent = "Показано " + n0(rows.length) + " из " + n0(items.length) + (qty ? " · к заказу сейчас " + n0(qty) + " шт." : "");
  }

  // --- карточка SKU -------------------------------------------------------------------------
  var modal = document.getElementById("sku-modal");
  var skuChart = null;
  function closeSku(fromRoute) {
    if (modal.hidden) return;
    modal.hidden = true;
    document.body.style.overflow = "";
    if (skuChart) { skuChart.dispose(); charts = charts.filter(function (c) { return c !== skuChart; }); skuChart = null; }
    if (!fromRoute && location.hash.indexOf("#sku=") === 0) history.replaceState(null, "", location.pathname + location.search + "#" + (document.querySelector(".tab.active") || { getAttribute: function () { return "summary"; } }).getAttribute("data-tab"));
  }
  function fact(label, value, small) { return "<dt>" + esc(label) + "</dt><dd>" + value + (small ? " <small>" + esc(small) + "</small>" : "") + "</dd>"; }
  function exRow(op, label, detail, v, cls) {
    return '<tr class="' + (cls || "") + '"><td class="op">' + op + "</td><td>" + esc(label) + (detail ? '<div class="d">' + detail + "</div>" : "") + '</td><td class="v">' + v + "</td></tr>";
  }
  function answer(r) {
    var e = r.explain || {};
    if (r.status === "order_now") return '<div class="sku-answer">Заказать <b>' + n0(r.order_qty) + " шт.</b> до <b>" + fdate(r.order_by) + "</b> у " + esc(r.supplier_id || "поставщика (не заведён)") + " · приход ≈ " + fdate(e.arrival) + "</div>";
    if (r.status === "order_later") return '<div class="sku-answer">Следующий заказ — до <b>' + fdate(r.order_by) + "</b>, ориентир <b>" + n0(r.order_qty) + " шт.</b> (оценка на дату заказа, уточнится при пересчёте)</div>";
    if (r.status === "ok") return '<div class="sku-answer neutral">Заказывать не нужно: остатка и товара в пути хватает на плечо и покрытие.</div>';
    if (r.status === "review_price") return '<div class="sku-answer neutral">В заказ не идёт: ' + esc(e.excluded || "на пересмотр цены") + (e.would_order ? ". Расчёт дал бы " + n0(e.would_order) + " шт." : "") + "</div>";
    return '<div class="sku-answer neutral">' + esc(e.excluded || "Мёртвый ассортимент") + (r.status === "dead_sell" ? " — распродать остаток " + n0(r.stock_now) + " шт." : " — закрыть карточку.") + "</div>";
  }
  function explainTable(r) {
    var e = r.explain || {}, R = r.cover_target_days, Lt = r.lead_days;
    var lotText = e.lot_source === "партия" ? "до кратности партии " + n0(e.lot) + " шт. (медиана приходов SKU)"
      : e.lot_source === "потолок" ? "не больше прогноза на " + (cfg.excess_cover_months || 12) + " мес (" + n0(e.cap) + " шт.)"
      : "до шага " + n0(e.lot) + " шт." + (e.lot_source === "шаг" && (r.flags || []).indexOf("партия больше годового спроса") >= 0 ? " — партия больше годового спроса"
        : e.lot_source === "шаг" && e.typical_lot > 1 ? " — до типичной партии " + n0(e.typical_lot) + " шт. не округляем: добавило бы больше " + n0(cfg.lot_max_overage_months || 1) + " мес. спроса" : "");
    var rows = [
      exRow("", "Прогноз спроса на окне покрытия", "от прихода заказа " + fdate(e.arrival) + " ещё R = " + n0(R) + " дн.", n1(e.forecast_horizon)),
      exRow("+", "Страховой запас", "z = " + (e.z === null || e.z === undefined ? "—" : e.z.toFixed(2).replace(".", ",")) + " · σ = " + n1(e.sigma_monthly) + " шт./мес по сезону окон: √(покрытие " + n1(e.safety_cover) + "² + плечо " + n1(e.safety_lead) + "²)" + (e.safety_cap !== undefined && e.safety <= e.safety_cap + 0.05 && Math.sqrt(Math.pow(e.safety_cover || 0, 2) + Math.pow(e.safety_lead || 0, 2)) > e.safety_cap + 0.05 ? ", не больше " + n0(cfg.safety_cap_months || 2) + " мес. спроса покрытия = " + n1(e.safety_cap) : ""), n1(e.safety)),
      exRow("−", "Остаток к приходу заказа", (e.lost_to_arrival > 0.05 && e.stock_at_arrival > 0.05
        // товар кончался, потом пришла партия в пути: потерянные продажи с неё не списываются (D12)
        ? "остаток " + n0(e.stock) + " + в пути до прихода " + n0(e.transit_lead) + " − продажи до прихода " + n1(e.forecast_lead - e.lost_to_arrival) + " (прогноз " + n1(e.forecast_lead) + ", из них " + n1(e.lost_to_arrival) + " потеряно, пока товара не было: партия в пути на них не уходит)"
        : "max(0; остаток " + n0(e.stock) + " + в пути до прихода " + n0(e.transit_lead) + " − прогноз до прихода " + n1(e.forecast_lead) + ")"), n1(e.stock_at_arrival)),
      exRow("−", "В пути с приходом в окно покрытия", "", n0(e.transit)),
      e.bridge > Math.max(e.gross_window || 0, 0) + 0.05
        // партия в пути придёт уже после того, как товар кончится: заказ закрывает разрыв до неё (D14)
        ? exRow("=", "Потребность", "по окну покрытия " + n1(e.gross_window) + ", но товар кончится раньше, чем придёт партия в пути, — заказ закрывает спрос до её прихода", n1(e.gross), "sub")
        : exRow("=", "Потребность", "", n1(e.gross), "sub")
    ];
    if (e.uplift && e.uplift !== 1) rows.push(exRow("×", "Поправка на недовоз поставщика", "доля исполнения ниже " + Math.round((cfg.reliability_min_ratio || 0.95) * 100) + "%", "×" + n1(e.uplift)));
    rows.push(exRow("⌈⌉", "Округление", esc(lotText), ""));
    if (e.merged_until_review) rows.push(exRow("+", "Следующий заказ до пересмотра", "по расчёту он понадобился бы раньше следующего пересмотра через " + n0(cfg.review_period_days || 30) + " дн. — до него не дожить, заказываем сразу", n0(e.merged_until_review)));
    rows.push(exRow("=", e.excluded ? "К заказу (исключено)" : "К заказу", e.excluded ? esc(e.excluded) : (r.status === "order_later" ? "оценка на " + fdate(e.at) : ""), n0(e.final) + " шт.", "total"));
    var extra = "";
    if (e.lost_lead > 0) extra += '<div class="callout red">До прихода заказа товара не будет ≈ ' + n0(e.days_short) + " дн., потеряно ≈ " + n0(e.lost_lead) + " шт. Эти продажи не дозаказываются — заказ закрывает только окно после прихода. Чтобы не терять продажи: местный поставщик или ускоренная доставка.</div>";
    if (e.excess_qty > 0) extra += '<div class="callout amber">Излишек: сверх ' + (cfg.excess_cover_months || 12) + " мес прогноза ≈ " + n0(e.excess_qty) + " шт.</div>";
    if (e.excluded && e.would_order) extra += '<div class="callout gray">Без исключения расчёт дал бы ' + n0(e.would_order) + " шт.</div>";
    return '<table class="explain"><tbody>' + rows.join("") + "</tbody></table>" + extra;
  }
  function openSku(id) {
    var r = byId[id];
    if (!r) return;
    var e = r.explain || {};
    var s = (D.series || {})[id] || {};
    var arr = (D.arrivals || {})[id] || [];
    var note = (D.sku_notes || {})[id];
    var sl = (cfg.service_level || {})[r.abc];
    var nextEta = r.next_eta ? fdate(r.next_eta) : "";
    var html = '<div class="sku-head"><h2 id="sku-title">' + esc(r.sku_id) + "</h2>" + statusBadge(r.status) + riskBadges(r.risk) + "</div>" +
      '<div class="sku-sub">' + esc(catLabel(r.category)) + " · поставщик " + esc(r.supplier_id || "не заведён") + " · ABC " + esc(r.abc || "—") +
      " · " + ({ "import": "импорт", "local": "местный", "unknown": "нет истории приходов" }[r.supply_type] || esc(r.supply_type)) + "</div>" +
      answer(r) +
      '<div class="sku-grid"><div>' +
        '<div class="box-title">Продажи, прогноз и остаток по месяцам</div><div class="chart" id="sku-chart" style="height:320px"></div>' +
        '<div class="box-title">Почему столько</div>' + explainTable(r) +
      "</div><div>" +
        '<div class="box-title">Факты</div><dl class="facts">' +
          fact("Остаток сейчас", n0(r.stock_now) + " шт.") +
          fact("В пути", n0(r.in_transit_qty) + " шт.", nextEta ? "ближайший " + nextEta : "") +
          fact("Хватит до", fdate(r.stockout_date), r.cover_days !== null ? n0(r.cover_days) + " дн." : coverText(r)) +
          fact("Плечо L", n0(r.lead_days) + " дн.", (L.lead_source || {})[r.lead_source] || "") +
          fact("Покрытие R", n0(r.cover_target_days) + " дн.") +
          fact("Страховой запас", n1(r.safety_stock) + " шт.", sl ? "сервис " + Math.round(sl * 100) + "%" : "") +
          fact("Прогноз на 12 мес", n0(r.forecast_12m) + " шт.") +
          fact("Продажи 12 мес", n0(r.sales_12m) + " шт.", n0(r.orders_12m) + " заказов") +
          fact("Возвраты 12 мес", n0(r.returns_12m) + " шт.", "не вычитаются") +
          fact("Ранг в категории", n0(r.rank_in_category)) +
          fact("Тренд", pctTrend(r.trend)) +
          fact("Наценка 12 мес", r.markup_12m === null ? "нет данных" : n0(r.markup_12m) + "%") +
          fact("Поправка на недовоз", "×" + n1(r.reliability_uplift)) +
          fact("История продаж", n0(r.history_months) + " мес") +
        "</dl>" +
        ((r.flags || []).length ? '<div class="box-title">Флаги</div><div>' + flagBadges(r.flags) + "</div>" : "") +
        (arr.length ? '<div class="box-title">Приходы в пути</div><table class="tbl compact"><tbody>' + arr.map(function (a) {
          return "<tr><td>" + esc(a.po_id || "—") + "</td><td>" + fdate(a.eta) + (a.late ? ' <span class="badge rk-late_po">опаздывает</span>' : "") + '</td><td class="n strong">' + n0(a.qty) + " шт.</td></tr>";
        }).join("") + "</tbody></table>" : "") +
        (note ? '<div class="box-title">Пояснение ИИ</div><div class="note-box"><span class="ai-tag">сгенерировано ИИ' + (D.note_model ? " (" + esc(D.note_model) + ")" : "") + ", числа — из расчёта системы</span><div>" + esc(note) + "</div></div>" : "") +
      "</div></div>";
    document.getElementById("sku-body").innerHTML = html;
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    modal.querySelector(".modal-box").scrollTop = 0;
    drawSkuChart(r, s, arr);
  }
  function drawSkuChart(r, s, arr) {
    if (skuChart) { skuChart.dispose(); charts = charts.filter(function (c) { return c !== skuChart; }); }
    skuChart = mkChart(document.getElementById("sku-chart"));
    if (!skuChart) return;
    var hist = s.history || [], fc = s.forecast || [], sp = s.stock_proj || [];
    var monthSet = {};
    hist.concat(fc, sp).forEach(function (p) { monthSet[p[0]] = 1; });
    arr.forEach(function (a) { monthSet[a.eta.slice(0, 7)] = 1; });
    var months = Object.keys(monthSet).sort();
    function mapSeries(pairs) { var m = {}; pairs.forEach(function (p) { m[p[0]] = p[1]; }); return months.map(function (k) { return m[k] === undefined ? null : m[k]; }); }
    var arrByMonth = {};
    arr.forEach(function (a) { var k = a.eta.slice(0, 7); arrByMonth[k] = (arrByMonth[k] || 0) + a.qty; });
    var asOfM = (D.as_of || "").slice(0, 7);
    var marks = [{ xAxis: fmonth(asOfM), label: { formatter: "сегодня", color: "#5f6b7b", fontSize: 10 }, lineStyle: { color: "#9aa3b0", type: "solid" } }];
    var e = r.explain || {};
    if (r.order_by && r.status === "order_later") marks.push({ xAxis: fmonth(r.order_by.slice(0, 7)), label: { formatter: "заказать до", color: COLORS.order, fontSize: 10 }, lineStyle: { color: COLORS.order } });
    var orderPoint = [];
    if ((r.status === "order_now" || r.status === "order_later") && e.arrival && r.order_qty) {
      var am = e.arrival.slice(0, 7);
      if (monthSet[am]) orderPoint = months.map(function (k) { return k === am ? r.order_qty : null; });
    }
    var series = [
      { name: "Продажи, факт", type: "bar", barMaxWidth: 14, data: mapSeries(hist), itemStyle: { color: COLORS.actual, borderRadius: [2, 2, 0, 0] },
        markLine: { symbol: "none", silent: true, data: marks, label: { position: "insideEndTop" } } },
      { name: "Прогноз спроса", type: "line", data: mapSeries(fc), symbol: "none", lineStyle: { color: COLORS.forecast, width: 2, type: "dashed" }, itemStyle: { color: COLORS.forecast } },
      { name: "Проекция остатка", type: "line", data: mapSeries(sp), symbol: "none", lineStyle: { color: COLORS.stock, width: 2 }, itemStyle: { color: COLORS.stock }, areaStyle: { color: "rgba(47,143,91,.08)" },
        markLine: r.safety_stock > 0 ? { symbol: "none", silent: true, data: [{ yAxis: r.safety_stock, label: { formatter: "страховой " + n0(r.safety_stock), color: COLORS.safety, fontSize: 10, position: "insideStartTop" } }], lineStyle: { color: COLORS.safety, type: "dotted", width: 1.5 } } : undefined },
      { name: "Приходы в пути", type: "scatter", symbol: "triangle", symbolSize: 12, data: months.map(function (k) { return arrByMonth[k] || null; }), itemStyle: { color: COLORS.transit } }
    ];
    if (orderPoint.length) series.push({ name: "Приход нового заказа", type: "scatter", symbol: "diamond", symbolSize: 13, data: orderPoint, itemStyle: { color: COLORS.order } });
    skuChart.setOption({
      textStyle: baseText, animation: false,
      grid: { left: 50, right: 14, top: 34, bottom: 26 },
      legend: { top: 0, left: 0, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11, color: "#5f6b7b" } },
      tooltip: { trigger: "axis", formatter: tooltipFmt },
      xAxis: Object.assign({ type: "category", data: months.map(fmonth) }, axisCommon(), { splitLine: { show: false } }),
      yAxis: Object.assign({ type: "value" }, axisCommon(), { axisLabel: { color: COLORS.axis, fontSize: 11, formatter: function (v) { return n0(v); } } }),
      series: series
    });
  }

  var INIT = { summary: initCalendar, skus: initSkus, accuracy: initAccuracy };
  route();
  if (!document.querySelector(".tab.active")) showTab("summary");
})();

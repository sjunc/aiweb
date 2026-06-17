/* ── State ── */
const PAGE_COUNT = 3;
let currentPage = 0;
let pageData = [{}, {}, {}];
let currentArticle = null;
let currentAnalysis = null;

/* ── DOM refs ── */
const tabBar = document.getElementById("tab-bar");
const tabs = tabBar.querySelectorAll(".tab-btn");
const track = document.getElementById("track");
const tmpl = document.getElementById("page-tmpl");

const modal = document.getElementById("article-modal");
const modalBackdrop = document.getElementById("modal-backdrop");
const modalClose = document.getElementById("modal-close");
const modalPress = document.getElementById("modal-press");
const modalDate = document.getElementById("modal-date");
const modalTitle = document.getElementById("modal-title");
const modalBody = document.getElementById("modal-body");
const modalLink = document.getElementById("modal-link");
const analysisLoading = document.getElementById("analysis-loading");
const analysisContent = document.getElementById("analysis-content");
const modalAlert = document.getElementById("modal-alert");
const modalView = document.getElementById("modal-view");
const modalCompare = document.getElementById("modal-compare");
const modalFc = document.getElementById("modal-fc");
const modalChatMsgs = document.getElementById("modal-chat-msgs");
const modalChatInput = document.getElementById("modal-chat-input");
const modalChatSend = document.getElementById("modal-chat-send");
const modalRetry = document.getElementById("modal-retry");
const subscribeForm = document.getElementById("subscribe-form");

const STANCE_COLORS = {
    far_left: { bg:'#eef2ff', text:'#4338ca', border:'#c7d2fe', thumb:'#6366f1' },
    left:     { bg:'#eff6ff', text:'#2563eb', border:'#bfdbfe', thumb:'#3b82f6' },
    center:   { bg:'#ecfdf5', text:'#059669', border:'#a7f3d0', thumb:'#10b981' },
    right:    { bg:'#fff7ed', text:'#d97706', border:'#fde68a', thumb:'#fb923c' },
    far_right:{ bg:'#fef2f2', text:'#dc2626', border:'#fecaca', thumb:'#ef4444' },
    unknown:  { bg:'#f8fafc', text:'#64748b', border:'#e2e8f0', thumb:'#94a3b8' },
};
const CATEGORIES = ["정치", "", "국제"];

/* ── Init ── */
document.addEventListener("DOMContentLoaded", () => {
    for (let i = 0; i < PAGE_COUNT; i++) {
        const clone = tmpl.content.cloneNode(true);
        const page = clone.firstElementChild;
        page.dataset.page = i;
        page.querySelector(".page-loading").id = "loading-" + i;
        page.querySelector(".page-error").id = "error-" + i;
        page.querySelector(".page-error-text").id = "error-text-" + i;
        page.querySelector(".page-retry").id = "retry-" + i;
        page.querySelector(".page-main").id = "main-" + i;
        page.querySelector(".main-card").id = "main-card-" + i;
        page.querySelector(".page-subs").id = "subs-" + i;
        page.querySelector(".sub-list").id = "sub-list-" + i;
        page.querySelector(".page-cards").id = "cards-" + i;
        page.querySelector(".card-grid").id = "card-grid-" + i;
        track.appendChild(page);
        pageData[i] = { loaded: false };
    }

    tabs.forEach((btn) => {
        btn.addEventListener("click", () => {
            const idx = parseInt(btn.dataset.page);
            goToPage(idx);
        });
    });

    // 모든 탭을 동시에 프리로드 (속도 향상)
    for (let i = 0; i < PAGE_COUNT; i++) loadPage(i);
    setTimeout(() => track.classList.add("track-ready"), 50);
    initDrag();

    modalClose.addEventListener("click", closeModal);
    modalBackdrop.addEventListener("click", closeModal);
    modalChatSend.addEventListener("click", sendChatMessage);
    modalChatInput.addEventListener("keypress", (e) => { if (e.key === "Enter") sendChatMessage(); });
    modalRetry.addEventListener("click", retryAnalysis);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

    subscribeForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const email = document.getElementById("subscribe-email").value.trim();
        if (email) { alert("구독 신청이 완료되었습니다 (데모)."); document.getElementById("subscribe-email").value = ""; }
    });
});

/* ── Page nav ── */
function goToPage(idx) {
    currentPage = idx;
    tabs.forEach((b, i) => b.classList.toggle("active", i === idx));
    track.style.transform = `translateX(-${idx * 100}%)`;
    if (!pageData[idx].loaded) loadPage(idx);
}

/* ── Drag / Swipe ── */
function initDrag() {
    let startX = 0, startY = 0, deltaX = 0, dragging = false;
    function onStart(x, y) {
        if (modal.classList.contains("active")) return;
        startX = x; startY = y; deltaX = 0; dragging = true;
        track.style.transition = "none";
    }
    function onMove(x, y) {
        if (!dragging) return;
        const dx = x - startX;
        const dy = y - startY;
        if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
        if (Math.abs(dx) < Math.abs(dy)) { dragging = false; return; }
        deltaX = dx;
        const base = -currentPage * 100;
        const pct = (dx / track.parentElement.offsetWidth) * 100;
        const clamp = (v) => Math.max(-(PAGE_COUNT-1)*100, Math.min(0, v));
        
        track.style.transform = `translateX(${clamp(base + pct)}%)`;
    }
    function onEnd() {
        if (!dragging) return;
        dragging = false;
        track.style.transition = "";
        const threshold = 60;
        if (deltaX < -threshold && currentPage < PAGE_COUNT - 1) goToPage(currentPage + 1);
        else if (deltaX > threshold && currentPage > 0) goToPage(currentPage - 1);
        else goToPage(currentPage);
    }
    
    // 전역 스와이프: 화면 어디를 터치해도 탭 전환
    document.addEventListener("touchstart", (e) => onStart(e.touches[0].clientX, e.touches[0].clientY), { passive: true });
    document.addEventListener("touchmove", (e) => onMove(e.touches[0].clientX, e.touches[0].clientY), { passive: true });
    document.addEventListener("touchend", onEnd, { passive: true });
    document.addEventListener("mousedown", (e) => onStart(e.clientX, e.clientY));
    document.addEventListener("mousemove", (e) => { if (dragging) onMove(e.clientX, e.clientY); });
    document.addEventListener("mouseup", onEnd);
}

/* ── API ── */
async function apiGet(url) {
    const resp = await fetch(url);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "요청 실패");
    return data;
}
async function apiPost(url, body) {
    const resp = await fetch(url, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "요청 실패");
    return data;
}

/* ── Data loading ── */
async function loadPage(idx) {
    const loading = document.getElementById("loading-" + idx);
    const error = document.getElementById("error-" + idx);
    const errorText = document.getElementById("error-text-" + idx);
    const retryBtn = document.getElementById("retry-" + idx);
    const mainSec = document.getElementById("main-" + idx);
    const mainCard = document.getElementById("main-card-" + idx);
    const subsSec = document.getElementById("subs-" + idx);
    const subList = document.getElementById("sub-list-" + idx);
    const cardsSec = document.getElementById("cards-" + idx);
    const cardGrid = document.getElementById("card-grid-" + idx);

    loading.classList.remove("hidden");
    error.classList.add("hidden");
    mainSec.classList.add("hidden");
    subsSec.classList.add("hidden");
    cardsSec.classList.add("hidden");

    const category = CATEGORIES[idx];
    const url = category ? `/api/trending?category=${encodeURIComponent(category)}` : "/api/trending";
    retryBtn.onclick = () => loadPage(idx);

    try {
        const data = await apiGet(url);
        const all = [data.main, ...(data.subs || [])].filter(Boolean);
        pageData[idx] = { loaded: true, articles: all };
        renderPage(all, mainCard, subList, cardGrid);
        loading.classList.add("hidden");
        if (all.length > 0) mainSec.classList.remove("hidden");
        if (all.length > 1) subsSec.classList.remove("hidden");
        if (all.length > 7) cardsSec.classList.remove("hidden");
    } catch (err) {
        loading.classList.add("hidden");
        error.classList.remove("hidden");
        errorText.textContent = err.message;
    }
}

function renderPage(all, mainCard, subList, cardGrid) {
    renderMainCard(all[0], mainCard);
    const headlines = all.slice(1, 7);
    subList.innerHTML = "";
    headlines.forEach((art, i) => subList.appendChild(createHeadline(art, i + 1)));
    const cards = all.slice(7, 15);
    cardGrid.innerHTML = "";
    cards.forEach((art) => cardGrid.appendChild(createMiniCard(art)));
}

/* ── Helpers ── */
function stanceColor(stance) {
    return (STANCE_COLORS[stance] || STANCE_COLORS.unknown).thumb;
}
function placeholderHTML(press, stance, size) {
    const s = "N";
    const color = stanceColor(stance);
    return `<span class="thumb-placeholder" style="width:${size}px;height:${size}px;background:${color}20;color:${color};border:1px solid ${color}40;font-size:${Math.round(size*0.38)}px;">${s}</span>`;
}

window.handleImageError = function(img, press, stance, size) {
    img.outerHTML = placeholderHTML(press, stance, size);
};

function biasBarMini(ml) {
    if (!ml) return "";
    const p = ml.progressive || 0;
    const c = ml.centrist || 0;
    const r = ml.conservative || 0;
    if (p + c + r === 0) return "";
    return `<div class="mini-bar"><div class="seg seg-prog" style="width:${p}%"></div><div class="seg seg-cent" style="width:${c}%"></div><div class="seg seg-cons" style="width:${r}%"></div></div><div class="mini-bar-label">진보 ${p}%  ·  중도 ${c}%  ·  보수 ${r}%</div>`;
}

function renderMainCard(art, el) {
    const barHTML = biasBarMini(art.ml_analysis);
    const imgHTML = art.image_url 
        ? `<img class="main-card-thumb" src="${art.image_url}" alt="news image" onerror="handleImageError(this, '${(art.press || "기타").replace(/'/g, "\\'")}', '${art.stance}', 80)" />`
        : placeholderHTML(art.press, art.stance, 80);
    el.innerHTML = `
        <div class="main-thumb-wrap">
            ${imgHTML}
            <div class="main-thumb-text">
                <div class="card-title">${art.title}</div>
            </div>
        </div>
        <div class="card-desc">${art.description || ""}</div>
        ${barHTML ? `<div class="card-bias">${barHTML}</div>` : ""}
        <div class="card-meta"><span>${art.pubDate || ""}</span><span>${art.category || ""}</span></div>
    `;
    el.onclick = () => openArticle(art);
}

function createHeadline(art, num) {
    const item = document.createElement("button");
    item.className = "sub-item";
    const color = stanceColor(art.stance);
    item.style.borderLeft = `3px solid ${color}`;
    item.style.paddingLeft = "0.6rem";
    const barHTML = biasBarMini(art.ml_analysis);
    item.innerHTML = `
        <span class="sub-num">${num}</span>
        <span class="sub-title">${art.title}</span>
        ${barHTML ? `<span class="sub-bar">${barHTML}</span>` : ""}
    `;
    item.onclick = () => openArticle(art);
    return item;
}

function createMiniCard(art) {
    const card = document.createElement("button");
    card.className = "mini-card";
    const color = stanceColor(art.stance);
    card.style.borderLeft = `3px solid ${color}`;
    const barHTML = biasBarMini(art.ml_analysis);
    const imgHTML = art.image_url 
        ? `<img class="mini-card-thumb" src="${art.image_url}" alt="news image" onerror="handleImageError(this, '${(art.press || "기타").replace(/'/g, "\\'")}', '${art.stance}', 48)" />`
        : placeholderHTML(art.press, art.stance, 48);
    card.innerHTML = `
        ${imgHTML}
        <div class="mini-text">
            <div class="mini-title">${art.title}</div>
            ${barHTML ? `<div class="mini-bar-wrap">${barHTML}</div>` : ""}
        </div>
    `;
    card.onclick = () => openArticle(art);
    return card;
}

/* ── Modal ── */
async function fetchBodyFast(art) {
    try {
        const result = await apiPost("/api/body", { article: art });
        if (currentArticle && currentArticle.link === art.link && result.body) {
            modalBody.textContent = result.body;
        }
    } catch (err) {
        console.error("Fast body fetch failed:", err);
    }
}

async function openArticle(art) {
    currentArticle = art;
    currentAnalysis = null;

    modalPress.textContent = "기사";
    modalPress.style.cssText = `border-left:3px solid ${stanceColor(art.stance)}; padding-left:0.5rem;`;
    modalDate.textContent = art.pubDate || "";
    modalTitle.textContent = art.title;
    modalBody.textContent = art.description || "";
    modalLink.href = art.link || "#";

    // Image in modal
    let modalImg = document.getElementById("modal-image");
    if (!modalImg) {
        modalImg = document.createElement("img");
        modalImg.id = "modal-image";
        modalImg.className = "modal-article-img";
        modalBody.parentNode.insertBefore(modalImg, modalBody);
    }
    if (art.image_url) {
        modalImg.src = art.image_url;
        modalImg.style.display = "block";
    } else {
        modalImg.style.display = "none";
    }

    analysisLoading.classList.remove("hidden");
    analysisContent.classList.add("hidden");
    modalAlert.innerHTML = "";
    modalView.textContent = "";
    modalCompare.textContent = "";
    modalFc.textContent = "";
    document.getElementById("modal-hl-neutral").textContent = "";
    document.getElementById("modal-hl-prog").textContent = "";
    document.getElementById("modal-hl-cons").textContent = "";
    modalChatMsgs.innerHTML = '<div class="chat-bubble assistant">이 기사에 대해 궁금한 점을 물어보세요.</div>';

    modal.classList.add("active");
    document.body.style.overflow = "hidden";

    fetchBodyFast(art);
    loadGeminiAnalysis(art);
}

async function loadGeminiAnalysis(artRef) {
    if (!currentArticle) return;
    artRef = artRef || currentArticle;
    
    analysisLoading.classList.remove("hidden");
    analysisContent.classList.add("hidden");
    
    const apiKeyInput = document.getElementById("custom-api-key");
    const customKey = apiKeyInput ? apiKeyInput.value.trim() : "";

    try {
        const result = await apiPost("/api/analyze", { article: artRef, api_key: customKey });
        // Prevent state contamination from previous clicks
        if (!currentArticle || currentArticle.link !== artRef.link) return;
        
        currentAnalysis = result;
        showGemini(result);
    } catch (err) {
        if (!currentArticle || currentArticle.link !== artRef.link) return;
        analysisLoading.classList.add("hidden");
        analysisContent.classList.remove("hidden");
        modalAlert.textContent = err.message;
    }
}

function showGemini(result) {
    analysisLoading.classList.add("hidden");
    analysisContent.classList.remove("hidden");

    if (result.article && result.article.body) {
        modalBody.textContent = result.article.body;
    }

    const gem = result.gemini_analysis || {};
    modalAlert.textContent = gem.bias_alert || "";
    modalView.textContent = gem.balanced_view || "";
    modalCompare.textContent = gem.comparison || "";
    modalFc.textContent = gem.fact_check || "";
    
    document.getElementById("modal-hl-neutral").textContent = gem.reframed_neutral || "";
    document.getElementById("modal-hl-prog").textContent = gem.reframed_progressive || "";
    document.getElementById("modal-hl-cons").textContent = gem.reframed_conservative || "";

    ["modal-alert", "modal-view", "modal-compare", "modal-fc"].forEach((id) => {
        const el = document.getElementById(id);
        const block = el.closest(".gemini-block");
        if (block) {
            block.style.display = el.textContent.trim() ? "" : "none";
        }
    });

    const hasHeadlines = (gem.reframed_neutral || gem.reframed_progressive || gem.reframed_conservative);
    const hlBlock = document.getElementById("modal-hl-neutral").closest(".gemini-block");
    if (hlBlock) {
        hlBlock.style.display = hasHeadlines ? "" : "none";
    }
}

function closeModal() {
    modal.classList.remove("active");
    document.body.style.overflow = "";
    currentArticle = null;
}

function retryAnalysis() {
    if (currentArticle) loadGeminiAnalysis(currentArticle);
}

async function sendChatMessage() {
    const msg = modalChatInput.value.trim();
    if (!msg || !currentArticle) return;
    const artRef = currentArticle;
    
    modalChatMsgs.insertAdjacentHTML('beforeend', `<div class="chat-bubble user">${escapeHtml(msg)}</div>`);
    modalChatInput.value = "";
    modalChatMsgs.scrollTop = modalChatMsgs.scrollHeight;
    modalChatSend.disabled = true;

    const apiKeyInput = document.getElementById("custom-api-key");
    const customKey = apiKeyInput ? apiKeyInput.value.trim() : "";

    try {
        const data = await apiPost("/api/perspective", { article: artRef, user_message: msg, api_key: customKey });
        if (!currentArticle || currentArticle.link !== artRef.link) return;
        
        modalChatMsgs.insertAdjacentHTML('beforeend', `<div class="chat-bubble assistant">${escapeHtml(data.reply)}</div>`);
        modalChatMsgs.scrollTop = modalChatMsgs.scrollHeight;
    } catch (err) {
        if (!currentArticle || currentArticle.link !== artRef.link) return;
        modalChatMsgs.insertAdjacentHTML('beforeend', `<div class="chat-bubble error">오류: ${escapeHtml(err.message)}</div>`);
    } finally { 
        if (currentArticle && currentArticle.link === artRef.link) {
            modalChatSend.disabled = false; 
            modalChatInput.focus(); 
        }
    }
}

function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
}

// GraphRAG Event Listener
document.addEventListener("DOMContentLoaded", () => {
    const btnExtract = document.getElementById("btn-extract-graph");
    const graphResult = document.getElementById("graph-rag-result");
    const graphCanvas = document.getElementById("graph-visualization");

    if (btnExtract) {
        btnExtract.onclick = async () => {
            if (!currentArticle) return;
            const artRef = currentArticle;
            btnExtract.disabled = true;
            btnExtract.textContent = "지식 그래프 시각화 중... (수십 초 소요될 수 있음)";
            graphResult.classList.add("hidden");
            graphCanvas.classList.add("hidden");

            try {
                const apiKeyInput = document.getElementById("custom-api-key");
                const customKey = apiKeyInput ? apiKeyInput.value.trim() : "";
                
                const result = await apiPost("/api/graph/extract", { article: artRef, api_key: customKey });
                if (!currentArticle || currentArticle.link !== artRef.link) return;
                
                // 1. Text result
                graphResult.textContent = result.subgraph_text || "추출된 그래프가 없습니다.";
                // graphResult.classList.remove("hidden"); // Only show visual graph to keep it clean

                // 2. Visual Graph Rendering
                if (result.subgraph_vis && result.subgraph_vis.nodes.length > 0) {
                    graphCanvas.classList.remove("hidden");
                    const data = {
                        nodes: new vis.DataSet(result.subgraph_vis.nodes),
                        edges: new vis.DataSet(result.subgraph_vis.edges)
                    };
                    const options = {
                        layout: {
                            hierarchical: {
                                direction: "UD",
                                sortMethod: "directed",
                                nodeSpacing: 150,
                                levelSeparation: 100
                            }
                        },
                        edges: {
                            font: { size: 12, align: 'middle' },
                            color: { color: '#ccc', highlight: '#848484' },
                            smooth: { type: 'cubicBezier' }
                        },
                        nodes: {
                            font: { size: 14, color: '#333' },
                            color: {
                                background: '#e0f7fa',
                                border: '#00bcd4',
                                highlight: { background: '#b2ebf2', border: '#0097a7' }
                            }
                        },
                        physics: {
                            hierarchicalRepulsion: { nodeDistance: 120 }
                        }
                    };
                    new vis.Network(graphCanvas, data, options);
                } else {
                    graphResult.textContent = "시각화할 그래프 데이터가 부족합니다.";
                    graphResult.classList.remove("hidden");
                }

            } catch (err) {
                graphResult.textContent = "오류: " + err.message;
                graphResult.classList.remove("hidden");
            } finally {
                btnExtract.disabled = false;
                btnExtract.textContent = "프레임 확산 그래프 시각화";
            }
        };
    }
});

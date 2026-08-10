const SUPPORTED_LANGUAGES = [
    'en', 'fr', 'de', 'es', 'it', 'pt', 'nl', 'ru', 'tr', 'sv',
    'pl', 'el', 'zh', 'no', 'da', 'ja', 'ko', 'hi', 'id', 'th',
    'ms', 'tl', 'ro'
];

const LANGUAGE_NAMES = {
    'en': 'English', 'fr': 'Français', 'de': 'Deutsch', 'es': 'Español',
    'it': 'Italiano', 'pt': 'Português', 'nl': 'Nederlands', 'ru': 'Русский',
    'tr': 'Türkçe', 'sv': 'Svenska', 'pl': 'Polski', 'el': 'Ελληνικά',
    'zh': '中文', 'no': 'Norsk', 'da': 'Dansk', 'ja': '日本語',
    'ko': '한국어', 'hi': 'हिन्दी', 'id': 'Bahasa', 'th': 'ไทย',
    'ms': 'Melayu', 'tl': 'Filipino', 'ro': 'Română'
};

const DEFAULT_LANGUAGE = 'en';

class I18n {
    constructor(basePath = './translations') {
        this.basePath = basePath;
        this.currentLang = this.detectLanguage();
        this.translations = {};
    }

    detectLanguage() {
        // 1. URL param (?lang=fr)
        try {
            const urlLang = new URLSearchParams(window.location.search).get('lang');
            if (urlLang && SUPPORTED_LANGUAGES.includes(urlLang)) {
                localStorage.setItem('pisum_lang', urlLang);
                localStorage.setItem('langExplicit', '1');
                return urlLang;
            }
        } catch(e) {}

        // 2. LocalStorage (pisum_lang ou preferredLanguage)
        try {
            const savedLang = localStorage.getItem('pisum_lang') || localStorage.getItem('preferredLanguage');
            if (savedLang && SUPPORTED_LANGUAGES.includes(savedLang)) {
                return savedLang;
            }
        } catch(e) {}

        // 3. Langues du navigateur
        const langs = (navigator.languages && navigator.languages.length)
            ? navigator.languages : [navigator.language];
        for (const lang of langs) {
            const code = lang.split('-')[0].toLowerCase();
            if (SUPPORTED_LANGUAGES.includes(code)) return code;
        }

        return DEFAULT_LANGUAGE;
    }

    async setLanguage(lang, explicit = false) {
        if (!SUPPORTED_LANGUAGES.includes(lang)) {
            lang = DEFAULT_LANGUAGE;
        }
        try {
            const response = await fetch(`${this.basePath}/${lang}.json?v=${Date.now()}`);
            if (!response.ok) throw new Error(`Could not load ${lang}.json`);

            this.translations = await response.json();
            this.currentLang = lang;
            document.documentElement.lang = lang;

            // Sauvegarde dans le localStorage
            localStorage.setItem('pisum_lang', lang);
            if (explicit) {
                localStorage.setItem('langExplicit', '1');
            }

            this.updateDOM();
            this.updateUI();

            document.dispatchEvent(new CustomEvent('languageChanged', { detail: { lang } }));
        } catch (error) {
            console.error('Translation loading failed:', error);
            if (lang !== DEFAULT_LANGUAGE) {
                this.setLanguage(DEFAULT_LANGUAGE, false);
            }
        }
    }

    updateDOM() {
        const elements = document.querySelectorAll('[data-i18n]');
        elements.forEach(el => {
            const key = el.getAttribute('data-i18n');
            const translation = this.getNestedTranslation(key);
            
            if (translation) {
                const attrMatch = key.match(/^\[(.*)\](.*)$/);
                if (attrMatch) {
                    el.setAttribute(attrMatch[1], translation);
                } else {
                    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                       if (el.type === 'button' || el.type === 'submit') {
                           el.value = translation;
                       } else {
                           el.placeholder = translation;
                       }
                    } else {
                        el.innerHTML = translation;
                    }
                }
            }
        });
    }

    getNestedTranslation(key) {
        let cleanKey = key;
        const attrMatch = key.match(/^\[(.*)\](.*)$/);
        if (attrMatch) cleanKey = attrMatch[2];
        return cleanKey.split('.').reduce((obj, k) => (obj || {})[k], this.translations);
    }

    updateUI() {
        const activeLangText = document.getElementById('pisum-active-lang');
        if (activeLangText) {
            activeLangText.textContent = this.currentLang.toUpperCase();
        }
        document.querySelectorAll('.pisum-lang-option').forEach(opt => {
            if (opt.dataset.lang === this.currentLang) {
                opt.classList.add('active');
            } else {
                opt.classList.remove('active');
            }
        });
    }
}

// Fonction globale pour changer la langue depuis n'importe où
window.changeLang = function(lang) {
    localStorage.setItem('pisum_lang', lang);
    if (window.i18n) {
        window.i18n.setLanguage(lang, true);
    } else {
        const url = new URL(window.location.href);
        url.searchParams.set('lang', lang);
        window.location.href = url.toString();
    }
};

window.setLanguage = window.changeLang;

// UI INJECTION LOGIC (Style Dark Glassmorphic Médical)
function injectFuturisticLangPicker() {
    if (document.querySelector('.pisum-lang-wrapper')) return;

    const style = document.createElement('style');
    style.innerHTML = `
        .pisum-lang-wrapper {
            position: relative;
            display: inline-block;
            font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
            z-index: 9999;
            margin-right: 12px;
            margin-bottom: 0;
        }
        .nav-right .pisum-lang-wrapper,
        .auth-nav .pisum-lang-wrapper {
            margin-bottom: 0;
        }
        .pisum-lang-btn {
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(12, 16, 28, 0.75);
            backdrop-filter: blur(16px) saturate(180%);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.12);
            padding: 7px 14px;
            border-radius: 99px;
            color: #FFFFFF;
            font-size: 0.82rem;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
        }
        @media(max-width:640px){
            .pisum-lang-wrapper { margin-right: 4px; }
            .pisum-lang-btn { padding: 6px 10px; font-size: 0.75rem; gap: 5px; }
            .pisum-lang-btn svg { width: 14px; height: 14px; }
            .pisum-lang-btn .icon-chevron { display: none; }
            .pisum-lang-dropdown { width: 190px; right: -10px; }
        }
        .pisum-lang-btn:hover {
            transform: translateY(-1px);
            box-shadow: 0 0 20px rgba(0, 229, 204, 0.25);
            background: rgba(18, 24, 38, 0.9);
            border-color: rgba(0, 229, 204, 0.4);
            color: #00E5CC;
        }
        .pisum-lang-btn svg {
            width: 15px;
            height: 15px;
            transition: transform 0.3s ease;
        }
        .pisum-lang-dropdown {
            position: absolute;
            top: calc(100% + 10px);
            right: 0;
            width: 210px;
            max-height: 340px;
            overflow-y: auto;
            background: rgba(9, 13, 22, 0.95);
            backdrop-filter: blur(20px) saturate(180%);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 18px;
            padding: 8px;
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.7);
            opacity: 0;
            visibility: hidden;
            transform: translateY(10px) scale(0.96);
            transform-origin: top right;
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .sb-bottom .pisum-lang-wrapper {
            margin-right: 0;
            width: 100%;
            margin-bottom: 12px;
        }
        .sb-bottom .pisum-lang-btn {
            width: 100%;
            justify-content: space-between;
            background: rgba(255, 255, 255, 0.04);
            border-radius: 12px;
            color: #94A3B8;
            border-color: rgba(255, 255, 255, 0.08);
            box-shadow: none;
        }
        .sb-bottom .pisum-lang-dropdown {
            bottom: calc(100% + 10px);
            top: auto;
            right: auto;
            left: 0;
            transform-origin: bottom left;
            transform: translateY(-10px) scale(0.96);
        }
        .pisum-lang-dropdown::-webkit-scrollbar {
            width: 5px;
        }
        .pisum-lang-dropdown::-webkit-scrollbar-thumb {
            background: rgba(255,255,255,0.15);
            border-radius: 10px;
        }
        .pisum-lang-wrapper.open .pisum-lang-dropdown {
            opacity: 1;
            visibility: visible;
            transform: translateY(0) scale(1);
        }
        .pisum-lang-wrapper.open .icon-chevron {
            transform: rotate(180deg);
        }
        .pisum-lang-option {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 14px;
            border-radius: 10px;
            color: #94A3B8;
            text-decoration: none;
            font-size: 0.83rem;
            font-weight: 600;
            transition: all 0.2s ease;
            cursor: pointer;
            border: none;
            background: transparent;
            width: 100%;
            text-align: left;
            margin-bottom: 2px;
        }
        .pisum-lang-option:last-child {
            margin-bottom: 0;
        }
        .pisum-lang-option:hover {
            background: rgba(0, 229, 204, 0.1);
            color: #FFFFFF;
            transform: translateX(3px);
        }
        .pisum-lang-option.active {
            background: rgba(0, 229, 204, 0.15);
            color: #00E5CC;
            font-weight: 800;
            box-shadow: inset 2px 0 0 #00E5CC;
        }
        .pisum-lang-option .lang-code {
            font-size: 0.65rem;
            opacity: 0.6;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-weight: 800;
        }
    `;
    document.head.appendChild(style);

    const wrapper = document.createElement('div');
    wrapper.className = 'pisum-lang-wrapper';
    
    let optionsHtml = '';
    SUPPORTED_LANGUAGES.forEach(code => {
        optionsHtml += `
            <button class="pisum-lang-option" data-lang="${code}">
                <span>${LANGUAGE_NAMES[code]}</span>
                <span class="lang-code">${code}</span>
            </button>
        `;
    });

    wrapper.innerHTML = `
        <div class="pisum-lang-btn" id="pisum-lang-btn">
            <div style="display:flex;align-items:center;gap:6px;">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>
                <span id="pisum-active-lang" style="letter-spacing:0.5px;">EN</span>
            </div>
            <svg class="icon-chevron" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
        </div>
        <div class="pisum-lang-dropdown">
            ${optionsHtml}
        </div>
    `;

    let target = document.querySelector('.nav-right');
    if (!target) target = document.querySelector('.sb-bottom');
    if (!target) target = document.querySelector('.auth-nav');
    
    if (target) {
        target.insertBefore(wrapper, target.firstChild);
    } else {
        wrapper.style.position = 'fixed';
        wrapper.style.top = '20px';
        wrapper.style.right = '20px';
        document.body.appendChild(wrapper);
    }

    const btn = wrapper.querySelector('#pisum-lang-btn');
    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        wrapper.classList.toggle('open');
    });

    document.addEventListener('click', (e) => {
        if (!wrapper.contains(e.target)) {
            wrapper.classList.remove('open');
        }
    });

    wrapper.querySelectorAll('.pisum-lang-option').forEach(btn => {
        btn.addEventListener('click', () => {
            const lang = btn.dataset.lang;
            if (window.i18n) {
                window.i18n.setLanguage(lang, true);
                wrapper.classList.remove('open');
            } else {
                window.changeLang(lang);
            }
        });
    });
}

function showLanguageBanner() {
    if (localStorage.getItem('langBannerDismissed') === '1') return;
    if (localStorage.getItem('langExplicit') === '1') return;
    if (document.getElementById('pisum-lang-banner')) return;

    const current = window.i18n ? window.i18n.currentLang : 'en';
    const picks = [
        { code: 'fr', label: '🇫🇷 Français' },
        { code: 'en', label: '🇬🇧 English' },
        { code: 'de', label: '🇩🇪 Deutsch' },
        { code: 'es', label: '🇪🇸 Español' },
        { code: 'it', label: '🇮🇹 Italiano' },
        { code: 'pt', label: '🇧🇷 Português' }
    ];

    const banner = document.createElement('div');
    banner.id = 'pisum-lang-banner';
    banner.style.cssText = [
        'position:fixed', 'bottom:90px', 'left:50%', 'transform:translateX(-50%)',
        'background:rgba(10, 12, 20, 0.95)', 'backdrop-filter:blur(16px)',
        'border:1px solid rgba(255,255,255,0.12)', 'border-radius:16px',
        'padding:0.85rem 1.1rem',
        'box-shadow:0 16px 48px rgba(0,0,0,0.7)',
        'z-index:9100', 'display:flex', 'align-items:center', 'gap:0.5rem',
        'flex-wrap:wrap', 'justify-content:center',
        'max-width:min(92vw,600px)', 'font-family:\'Plus Jakarta Sans\',-apple-system,sans-serif'
    ].join(';');

    const dismiss = () => {
        banner.remove();
        localStorage.setItem('langBannerDismissed', '1');
    };

    const label = document.createElement('span');
    label.textContent = '🌐';
    label.style.cssText = 'color:#00E5CC;font-size:0.8rem;flex-shrink:0';

    const closeBtn = document.createElement('button');
    closeBtn.textContent = '✕';
    closeBtn.style.cssText = 'background:none;border:none;color:#94A3B8;cursor:pointer;font-size:0.9rem;line-height:1;padding:0 4px;flex-shrink:0';
    closeBtn.addEventListener('click', dismiss);

    banner.appendChild(label);
    picks.forEach(l => {
        const isActive = l.code === current;
        const b = document.createElement('button');
        b.textContent = l.label;
        b.style.cssText = [
            'border-radius:999px', 'padding:0.35rem 0.85rem', 'font-size:0.78rem',
            'font-weight:700', 'cursor:pointer', 'font-family:inherit',
            'white-space:nowrap',
            isActive
                ? 'background:#00E5CC;color:#000;border:1px solid #00E5CC'
                : 'background:rgba(255,255,255,0.05);color:#E2E8F0;border:1px solid rgba(255,255,255,0.1)'
        ].join(';');
        b.addEventListener('click', () => {
            if (window.i18n) {
                window.i18n.setLanguage(l.code, true);
                dismiss();
            } else {
                window.changeLang(l.code);
            }
        });
        banner.appendChild(b);
    });
    banner.appendChild(closeBtn);

    document.body.appendChild(banner);
}

// Initialisation globale au chargement du DOM
document.addEventListener('DOMContentLoaded', () => {
    window.i18n = new I18n('./translations');
    injectFuturisticLangPicker();
    window.i18n.setLanguage(window.i18n.currentLang).then(() => {
        setTimeout(showLanguageBanner, 800);
    });
});
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
        // Only use saved lang if user explicitly chose it (not auto-detected)
        const savedLang = localStorage.getItem('preferredLanguage');
        const isExplicit = localStorage.getItem('langExplicit') === '1';
        if (savedLang && isExplicit && SUPPORTED_LANGUAGES.includes(savedLang)) {
            return savedLang;
        }
        // Check all browser preferred languages (navigator.languages is more complete)
        const langs = (navigator.languages && navigator.languages.length)
            ? navigator.languages
            : [navigator.language];
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
            const response = await fetch(`${this.basePath}/${lang}.json`);
            if (!response.ok) throw new Error(`Could not load ${lang}.json`);

            this.translations = await response.json();
            this.currentLang = lang;
            document.documentElement.lang = lang;

            // Only persist to localStorage when user explicitly picks a language
            if (explicit) {
                localStorage.setItem('preferredLanguage', lang);
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

// UI INJECTION LOGIC
function injectFuturisticLangPicker() {
    // 1. Inject CSS
    const style = document.createElement('style');
    style.innerHTML = `
        .pisum-lang-wrapper {
            position: relative;
            display: inline-block;
            font-family: 'Sora', -apple-system, sans-serif;
            z-index: 9999;
            margin-right: 15px;
            margin-bottom: 10px;
        }
        .nav-right .pisum-lang-wrapper,
        .auth-nav .pisum-lang-wrapper {
            margin-bottom: 0;
        }
        .pisum-lang-btn {
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(16px) saturate(180%);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(0, 0, 0, 0.08);
            padding: 8px 18px;
            border-radius: 99px;
            color: #09090f;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.25, 1, 0.5, 1);
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.04), inset 0 0 0 1px rgba(255, 255, 255, 0.4);
        }
        .pisum-lang-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 12px 30px rgba(59, 110, 248, 0.2), 0 4px 10px rgba(59, 110, 248, 0.1), inset 0 0 0 1px rgba(255, 255, 255, 0.6);
            background: rgba(255,255,255,0.9);
            border-color: rgba(59, 110, 248, 0.5);
            color: #3b6ef8;
        }
        .pisum-lang-btn svg {
            width: 16px;
            height: 16px;
            transition: transform 0.3s ease;
        }
        .pisum-lang-dropdown {
            position: absolute;
            top: calc(100% + 12px);
            right: 0;
            width: 220px;
            max-height: 380px;
            overflow-y: auto;
            background: rgba(255, 255, 255, 0.85);
            backdrop-filter: blur(24px) saturate(200%);
            -webkit-backdrop-filter: blur(24px);
            border: 1px solid rgba(255, 255, 255, 0.8);
            border-radius: 20px;
            padding: 10px;
            box-shadow: 0 24px 48px rgba(0, 0, 0, 0.12), 0 8px 16px rgba(0, 0, 0, 0.04), 0 0 0 1px rgba(0,0,0,0.02);
            opacity: 0;
            visibility: hidden;
            transform: translateY(15px) scale(0.92);
            transform-origin: top right;
            transition: all 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
        }
        /* Dashboard specific fallback styling */
        .sb-bottom .pisum-lang-wrapper {
            margin-right: 0;
            width: 100%;
            margin-bottom: 15px;
        }
        .sb-bottom .pisum-lang-btn {
            width: 100%;
            justify-content: space-between;
            background: transparent;
            border-radius: 12px;
            color: var(--t2, #64748b);
            border-color: var(--border, #e2e8f0);
            box-shadow: none;
        }
        .sb-bottom .pisum-lang-dropdown {
            bottom: calc(100% + 12px);
            top: auto;
            right: auto;
            left: 0;
            transform-origin: bottom left;
            transform: translateY(-15px) scale(0.92);
        }
        .sb-bottom .pisum-lang-wrapper.open .pisum-lang-dropdown {
            transform: translateY(0) scale(1);
        }

        .pisum-lang-dropdown::-webkit-scrollbar {
            width: 6px;
        }
        .pisum-lang-dropdown::-webkit-scrollbar-thumb {
            background: rgba(0,0,0,0.15);
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
            padding: 12px 16px;
            border-radius: 12px;
            color: #3a3a4c;
            text-decoration: none;
            font-size: 0.85rem;
            font-weight: 500;
            transition: all 0.25s ease;
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
            background: rgba(59, 110, 248, 0.08);
            color: #3b6ef8;
            transform: translateX(4px);
        }
        .pisum-lang-option.active {
            background: linear-gradient(135deg, rgba(59, 110, 248, 0.15) 0%, rgba(124, 90, 240, 0.15) 100%);
            color: #3b6ef8;
            font-weight: 700;
            box-shadow: inset 2px 0 0 #3b6ef8;
        }
        .pisum-lang-option .lang-code {
            font-size: 0.65rem;
            opacity: 0.6;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-weight: 700;
        }
    `;
    document.head.appendChild(style);

    // 2. Create HTML
    const wrapper = document.createElement('div');
    wrapper.className = 'pisum-lang-wrapper';
    
    let optionsHtml = '';
    SUPPORTED_LANGUAGES.forEach(code => {
        optionsHtml += \`
            <button class="pisum-lang-option" data-lang="\${code}">
                <span>\${LANGUAGE_NAMES[code]}</span>
                <span class="lang-code">\${code}</span>
            </button>
        \`;
    });

    wrapper.innerHTML = \`
        <div class="pisum-lang-btn" id="pisum-lang-btn">
            <div style="display:flex;align-items:center;gap:8px;">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>
                <span id="pisum-active-lang" style="letter-spacing:0.5px;">EN</span>
            </div>
            <svg class="icon-chevron" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
        </div>
        <div class="pisum-lang-dropdown">
            \${optionsHtml}
        </div>
    \`;

    // 3. Find Insertion Point
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

    // 4. Events
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
            window.i18n.setLanguage(lang, true); // explicit = save to localStorage
            wrapper.classList.remove('open');
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
        { code: 'pt', label: '🇧🇷 Português' },
        { code: 'ar', label: '🇸🇦 عربي' },
    ];

    const banner = document.createElement('div');
    banner.id = 'pisum-lang-banner';
    banner.style.cssText = [
        'position:fixed', 'bottom:90px', 'left:50%', 'transform:translateX(-50%)',
        'background:#ffffff', 'border:1px solid rgba(0,0,0,0.09)',
        'border-radius:16px', 'padding:0.85rem 1.1rem',
        'box-shadow:0 12px 40px rgba(0,0,0,0.13),0 2px 8px rgba(0,0,0,0.06)',
        'z-index:9100', 'display:flex', 'align-items:center', 'gap:0.5rem',
        'flex-wrap:wrap', 'justify-content:center',
        'max-width:min(92vw,600px)', 'font-family:\'Sora\',-apple-system,sans-serif'
    ].join(';');

    const dismiss = () => {
        banner.remove();
        localStorage.setItem('langBannerDismissed', '1');
    };

    const label = document.createElement('span');
    label.textContent = '🌐';
    label.style.cssText = 'color:#8a8aa8;font-size:0.8rem;flex-shrink:0';

    const closeBtn = document.createElement('button');
    closeBtn.textContent = '✕';
    closeBtn.style.cssText = 'background:none;border:none;color:#bbbbd0;cursor:pointer;font-size:0.9rem;line-height:1;padding:0 4px;flex-shrink:0';
    closeBtn.addEventListener('click', dismiss);

    banner.appendChild(label);
    picks.forEach(l => {
        const isActive = l.code === current;
        const b = document.createElement('button');
        b.textContent = l.label;
        b.style.cssText = [
            'border-radius:999px', 'padding:0.32rem 0.85rem', 'font-size:0.78rem',
            'font-weight:600', 'cursor:pointer', 'font-family:inherit',
            'white-space:nowrap',
            isActive
                ? 'background:#09090f;color:#fff;border:1px solid #09090f'
                : 'background:transparent;color:#3a3a4c;border:1px solid rgba(0,0,0,0.12)'
        ].join(';');
        b.addEventListener('click', () => {
            window.i18n && window.i18n.setLanguage(l.code, true);
            dismiss();
        });
        banner.appendChild(b);
    });
    banner.appendChild(closeBtn);

    document.body.appendChild(banner);
}

document.addEventListener('DOMContentLoaded', () => {
    // Both SiteWeb and saas/frontend will have a 'translations' folder next to the HTML files
    window.i18n = new I18n('./translations');
    injectFuturisticLangPicker();
    window.i18n.setLanguage(window.i18n.currentLang).then(() => {
        setTimeout(showLanguageBanner, 800);
    });
});

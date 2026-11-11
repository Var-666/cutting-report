"""Build a script-free email body from the already archived report."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class EmailReport:
    html: str
    images: dict[str, bytes]


# Inline the archived document's own styles; only translate unsupported layout.
LAYOUT_NODES = '.cover-brand,.brand-mark,.cover-main,.cover-folio,.meta,.metrics,.secondary,.analysis-head,.analysis-facts,.analysis-grid,.analysis-findings,.analysis-followup,.data-chart-grid,.chart-stack,.report-footer,.content-grid'

EMAIL_DOCUMENT = """({mobileLayouts, breakpoint, pageInset}) => {
    const output = document.implementation.createHTMLDocument(document.title);
    output.documentElement.lang = document.documentElement.lang;
    const responsive = [];
    const properties = [
        'box-sizing','color','background-color','background-image','font-family','font-size',
        'font-weight','font-style','line-height','letter-spacing','text-align','white-space',
        'padding-top','padding-right','padding-bottom','padding-left',
        'margin-top','margin-right','margin-bottom','margin-left',
        'border-radius','box-shadow','vertical-align','overflow-wrap','word-break',
        'text-overflow','overflow','opacity','min-height','max-height','border-collapse',
    ];
    const inherited = new Set(['color','font-family','font-size','font-weight','font-style',
        'line-height','letter-spacing','text-align','white-space','overflow-wrap','word-break']);
    const defaults = {
        'background-color':'rgba(0, 0, 0, 0)', 'background-image':'none',
        'border-radius':'0px', 'box-shadow':'none', 'vertical-align':'baseline',
        'text-overflow':'clip', 'overflow':'visible', 'opacity':'1',
        'min-height':'0px', 'max-height':'none', 'border-collapse':'separate',
    };
    function apply(source, target, pseudo = null) {
        const style = getComputedStyle(source, pseudo);
        const parent = source.parentElement ? getComputedStyle(source.parentElement) : null;
        for (const property of properties) {
            const value = style.getPropertyValue(property);
            if (inherited.has(property) && parent?.getPropertyValue(property) === value && !pseudo) continue;
            if (defaults[property] === value) continue;
            if (property.startsWith('margin-') && value === '0px' && !source.matches('body,p,h1,h2,h3,ul,ol,dl,dd')) continue;
            if (property.startsWith('padding-') && value === '0px' && !source.matches('ul,ol')) continue;
            target.style.setProperty(property, value);
        }
        for (const side of ['top','right','bottom','left']) {
            const width = style.getPropertyValue(`border-${side}-width`);
            if (Number.parseFloat(width)) target.style.setProperty(`border-${side}`, `${width} ${style.getPropertyValue(`border-${side}-style`)} ${style.getPropertyValue(`border-${side}-color`)}`);
        }
        target.style.display = ['grid','flex'].includes(style.display) ? 'block' : style.display;
        return style;
    }
    function presentation() {
        const table = output.createElement('table');
        for (const [key, value] of Object.entries({role:'presentation',width:'100%',cellpadding:'0',cellspacing:'0',border:'0'})) table.setAttribute(key,value);
        table.style.cssText = 'width:100%;table-layout:fixed;border-collapse:collapse;border:0;white-space:normal;';
        return table;
    }
    function spacer(row, width) {
        const cell = row.insertCell();
        cell.style.cssText = `width:${width}px;padding:0;border:0;font-size:0;line-height:0;`;
        cell.setAttribute('width',String(width));
        cell.setAttribute('aria-hidden','true');
    }
    function layout(source, target, style) {
        const nodes = Array.from(source.children);
        const grid = style.display === 'grid';
        const tracks = grid ? style.gridTemplateColumns.split(' ').map(Number.parseFloat).filter(width => width > 0) : nodes.map(node => node.getBoundingClientRect().width);
        const count = tracks.length;
        const gap = Number.parseFloat(style.columnGap) || 0;
        const rowGap = Number.parseFloat(style.rowGap) || 0;
        const total = tracks.reduce((sum,value) => sum + value,0);
        const table = presentation();
        const key = 'email-layout-' + source.dataset.emailLayout;
        table.className = key;
        nodes.forEach((node,index) => {
            if (index && index % count === 0 && rowGap) {
                const space = table.insertRow().insertCell();
                space.colSpan = count * 2 - 1;
                space.style.cssText = `height:${rowGap}px;padding:0;border:0;font-size:0;line-height:0;`;
                space.className = 'email-gap';
                space.setAttribute('aria-hidden','true');
            }
            const row = index % count === 0 ? table.insertRow() : table.rows[table.rows.length - 1];
            if (index % count && gap) spacer(row,gap);
            const wrap = !grid || node.matches('.chart,.table-wrap');
            const cell = wrap ? output.createElement('td') : copy(node,'td');
            const ratio = tracks[index % count] / total;
            cell.style.width = `${ratio * 100}%`;
            cell.setAttribute('width',String(ratio * 100) + '%');
            if (source.matches('.cover-main') && count === 2) {
                cell.style.width = index === 0 ? `calc(100% - ${tracks[1]}px)` : `${tracks[1]}px`;
                cell.removeAttribute('width');
            }
            cell.style.verticalAlign = style.alignItems === 'center' ? 'middle' : style.alignItems === 'flex-end' ? 'bottom' : 'top';
            if (grid) cell.style.height = `${node.getBoundingClientRect().height}px`;
            if (wrap) {
                cell.style.padding = '0';
                cell.style.border = '0';
                if (!grid && style.justifyContent !== 'space-between') {
                    cell.style.width = index === nodes.length - 1 ? 'auto' : `${tracks[index]}px`;
                    cell.removeAttribute('width');
                } else if (!grid && index === nodes.length - 1) cell.style.textAlign = 'right';
                cell.append(copy(node));
            }
            cell.classList.add('email-cell');
            row.append(cell);
        });
        if (!grid) {
            const height = source.getBoundingClientRect().height - ['paddingTop','paddingBottom','borderTopWidth','borderBottomWidth'].reduce((sum,key)=>sum+(Number.parseFloat(style[key])||0),0);
            table.style.height = `${height}px`;
        }
        if (grid) {
            const columns = mobileLayouts[source.dataset.emailLayout];
            if (columns) responsive.push(`.${key},.${key}>tbody{display:block!important;width:100%!important;} .${key}>tbody>tr{display:block!important;} .${key}>tbody>tr>.email-cell{display:inline-block!important;width:calc(${100 / columns}% - ${gap}px)!important;height:auto!important;margin:${rowGap / 2}px ${gap / 2}px!important;vertical-align:top!important;box-sizing:border-box!important;} .${key}>tbody>tr>td:not(.email-cell){display:none!important;}`);
        }
        target.append(table);
    }
    const allowed = new Set(['div','header','footer','main','section','article','h1','h2','h3','h4','p','span','small','strong','b','em','i','br','table','colgroup','col','thead','tbody','tfoot','tr','th','td','ul','ol','li','dl','dt','dd']);
    function copy(node, tag = null) {
        if (node.nodeType === Node.TEXT_NODE) return output.createTextNode(node.textContent);
        if (node.nodeType !== Node.ELEMENT_NODE) return null;
        if (node.matches('script,style,link,meta,iframe,object,embed,form,button,input,img,svg,canvas,.print-only,.analysis-metric-icon')) return null;
        if (node.dataset.emailCid) {
            const frame = output.createElement('div');
            frame.className = node.className;
            const style = apply(node,frame);
            frame.style.width = node.matches('.analysis-sparkline') ? style.width : '100%';
            frame.style.height = style.height;
            const image = output.createElement('img');
            image.src = 'cid:' + node.dataset.emailCid;
            image.alt = node.getAttribute('aria-label') || '报表图表';
            image.width = Math.round(node.getBoundingClientRect().width);
            image.style.cssText = 'display:block;width:100%;height:100%;object-fit:contain;border:0;';
            frame.append(image);
            return frame;
        }
        const element = output.createElement(tag || (allowed.has(node.tagName.toLowerCase()) ? node.tagName.toLowerCase() : 'div'));
        element.className = node.className;
        for (const attribute of ['colspan','rowspan','title','aria-label','role']) {
            if (node.hasAttribute(attribute)) element.setAttribute(attribute,node.getAttribute(attribute));
        }
        const style = apply(node,element);
        if (tag === 'td') element.style.display = 'table-cell';
        if (node.matches('.page')) {
            element.style.width = '100%';
            element.style.maxWidth = style.width;
            element.style.marginLeft = 'auto';
            element.style.marginRight = 'auto';
        }
        if (node.matches('.table-wrap')) element.style.width = '100%';
        if (node.matches('.table-wrap')) {
            element.style.height = 'auto';
            element.style.maxHeight = 'none';
        }
        if (node.matches('table')) {
            element.style.width = '100%';
            element.style.minWidth = style.minWidth;
            element.style.tableLayout = style.tableLayout;
        }
        if (node.matches('th,td')) {
            element.style.height = style.height;
            if (node.style.width) element.style.width = node.style.width;
        }
        if (node.matches('col')) element.style.width = node.style.width;
        if (node.matches('.brand-mark,.brand-mark i')) {
            element.style.width = style.width;
            element.style.height = style.height;
        }
        if (node.matches('.metric')) {
            const stripe = getComputedStyle(node,'::before');
            if (stripe.content !== 'none') {
                element.style.borderTop = `${stripe.height} solid ${stripe.backgroundColor}`;
                element.style.paddingTop = `${Math.max(0,Number.parseFloat(style.paddingTop)-Number.parseFloat(stripe.height))}px`;
            }
        }
        if (node.matches('h2')) {
            const decoration = getComputedStyle(node,'::after');
            if (decoration.content !== 'none') {
                const stripe = output.createElement('span');
                apply(node,stripe,'::after');
                stripe.style.cssText += `display:inline-block;width:${decoration.width};height:${decoration.height};margin-right:${style.gap};vertical-align:middle;`;
                stripe.setAttribute('aria-hidden','true');
                element.append(stripe);
            }
        }
        if (node.dataset.emailLayout && ['grid','flex'].includes(style.display)) {
            layout(node,element,style);
        } else {
            for (const child of node.childNodes) {
                const copied = copy(child);
                if (copied) element.append(copied);
            }
        }
        return element;
    }
    apply(document.body,output.body);
    output.body.style.padding = `0 ${pageInset}px`;
    const accent = getComputedStyle(document.body,'::before');
    if (accent.content !== 'none') {
        const stripe = output.createElement('div');
        stripe.style.cssText = `height:${accent.height};margin:0 -${pageInset}px -${accent.height};background:${accent.backgroundImage};`;
        stripe.setAttribute('aria-hidden','true');
        output.body.append(stripe);
    }
    for (const node of document.body.childNodes) {
        const copied = copy(node);
        if (copied) output.body.append(copied);
    }
    const meta = output.createElement('meta');
    meta.name = 'viewport';
    meta.content = 'width=device-width,initial-scale=1';
    output.head.append(meta);
    const charset = output.createElement('meta');
    charset.setAttribute('charset','utf-8');
    output.head.prepend(charset);
    const mobile = output.createElement('style');
    // Preserve the source's own responsive rules, overriding its inlined desktop styles.
    for (const sheet of document.styleSheets) {
        for (const rule of sheet.cssRules) {
            if (rule.type !== CSSRule.MEDIA_RULE || rule.conditionText.includes('print')) continue;
            for (const item of rule.cssRules) {
                if (item.type !== CSSRule.STYLE_RULE) continue;
                const declarations = Array.from(item.style,property => `${property}:${item.style.getPropertyValue(property)}!important;`).join('');
                mobile.textContent += `@media ${rule.conditionText}{${item.selectorText}{${declarations}}}`;
            }
        }
    }
    const root = getComputedStyle(document.documentElement);
    for (const property of root) if (property.startsWith('--')) output.documentElement.style.setProperty(property,root.getPropertyValue(property));
    mobile.textContent += `@media(max-width:${breakpoint}px){${responsive.join('')}}`;
    output.head.append(mobile);
    return '<!doctype html>' + output.documentElement.outerHTML;
}"""


def render_email_report(html_path: Path, output_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    images = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.route("**/*", lambda route: route.abort())
            html = html_path.read_text(encoding="utf-8")
            page.set_content(html, wait_until="load", timeout=30_000)
            if "window.__REPORT_READY__" in html:
                page.wait_for_function("window.__REPORT_READY__ === true", timeout=30_000)
            page.evaluate("""selector => document.querySelectorAll(selector).forEach((node,index) => {
                node.dataset.emailLayout = String(index + 1);
            })""", LAYOUT_NODES)
            page.set_viewport_size({"width": 375, "height": 900})
            mobile_layouts = page.evaluate("""selector => Object.fromEntries(Array.from(document.querySelectorAll(selector),node => [
                node.dataset.emailLayout, getComputedStyle(node).gridTemplateColumns.split(' ').map(Number.parseFloat).filter(width => width > 0).length
            ]))""", LAYOUT_NODES)
            page_inset = page.evaluate("document.querySelector('.page')?.getBoundingClientRect().left || 0")
            breakpoint = page.evaluate(r"""() => Math.max(0,...Array.from(document.styleSheets).flatMap(sheet =>
                Array.from(sheet.cssRules,rule => rule.type === CSSRule.MEDIA_RULE && !rule.conditionText.includes('print')
                    ? Number.parseFloat(rule.conditionText.match(/max-width:\s*([0-9.]+)/)?.[1] || '0') : 0)))""")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.evaluate("""async () => {
                await document.fonts.ready;
                document.querySelectorAll('.chart').forEach(element => {
                    const chart = window.echarts?.getInstanceByDom(element);
                    if (!chart) throw new Error('报表图表尚未生成');
                    chart.setOption({animation:false});
                    chart.resize({animation:{duration:0}});
                });
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }""")
            for index, chart in enumerate(page.locator('.chart,.analysis-sparkline').all()):
                cid = f"report-chart-{index}@cutting-report"
                surface = chart.locator("canvas,svg").first
                images[cid] = base64.b64encode(surface.screenshot(type="png", timeout=30_000)).decode("ascii")
                chart.evaluate("(element, cid) => { element.dataset.emailCid = cid; }", cid)
            document = page.evaluate(EMAIL_DOCUMENT, {"mobileLayouts": mobile_layouts, "breakpoint": breakpoint, "pageInset": page_inset})
        finally:
            browser.close()
    output_path.write_text(json.dumps({"html": document, "images": images}), encoding="utf-8")

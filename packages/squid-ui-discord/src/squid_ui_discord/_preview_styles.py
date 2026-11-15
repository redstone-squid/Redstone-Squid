"""Styles for the standalone and embedded Discord HTML previews."""

PREVIEW_CSS = """
.squid-view {
    display: grid;
    gap: 12px;
    box-sizing: border-box;
    max-width: 720px;
    padding: 16px;
    border-radius: 8px;
    background: #313338;
    color: #dbdee1;
    font: 14px/1.375 system-ui,sans-serif;
}

.squid-view * {
    box-sizing: border-box;
}

.squid-panel {
    display: grid;
    gap: 12px;
    padding: 16px;
    border-left: 4px solid var(--squid-accent,#4e5058);
    border-radius: 4px;
    background: #2b2d31;
}

.squid-text {
    white-space: pre-wrap;
}

.squid-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
}

.squid-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    margin: 0;
    min-height: 32px;
    padding: 2px 16px;
    border: 0;
    border-radius: 3px;
    background: #4e5058;
    color: #fff;
    font: inherit;
    font-weight: 600;
    text-align: center;
}

.squid-button--primary {
    background: #5865f2;
}

.squid-button--success {
    background: #248046;
}

.squid-button--danger {
    background: #da373c;
}

.squid-button:disabled {
    opacity: .5;
}

.squid-link {
    text-decoration: none;
}

.squid-select {
    width: 100%;
    min-height: 40px;
    padding: 8px;
    border: 1px solid #1e1f22;
    border-radius: 3px;
    background: #1e1f22;
    color: #dbdee1;
}

.squid-section {
    display: grid;
    grid-template-columns: minmax(0,1fr) auto;
    gap: 16px;
    align-items: center;
}

.squid-section>div {
    display: grid;
    gap: 8px;
}

.squid-text>:first-child {
    margin-top: 0;
}

.squid-text>:last-child {
    margin-bottom: 0;
}

.squid-thumbnail {
    max-width: 96px;
    max-height: 96px;
    border-radius: 4px;
}

.squid-gallery {
    display: grid;
    grid-template-columns: repeat(auto-fit,minmax(120px,1fr));
    gap: 4px;
}

.squid-gallery img {
    width: 100%;
    border-radius: 4px;
}

.squid-separator {
    height: 1px;
    margin: 4px 0;
    border: 0;
    background: #3f4147;
}

.squid-separator--large {
    margin: 12px 0;
}

.squid-extension {
    padding: 8px;
    border: 1px dashed #6d6f78;
    border-radius: 3px;
}

.squid-file[aria-disabled=true] {
    opacity: .5;
}

.squid-subtext {
    font-size: 12px;
    color: #949ba4;
}

.squid-text-spoiler {
    position: relative;
    border-radius: 3px;
    background: #1e1f22;
    cursor: pointer;
    box-decoration-break: clone;
    -webkit-box-decoration-break: clone;
}

.squid-text-spoiler>input {
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
    margin: 0;
}

.squid-text-spoiler__content {
    visibility: hidden;
}

.squid-text-spoiler:has(>input:checked) {
    background: #ffffff1a;
}

.squid-text-spoiler>input:checked+.squid-text-spoiler__content {
    visibility: visible;
}

.squid-text-spoiler:has(>input:focus-visible) {
    outline: 2px solid #00a8fc;
    outline-offset: 2px;
}

.squid-spoiler {
    filter: blur(12px);
    transition: filter .15s;
}

.squid-spoiler:hover,.squid-spoiler:focus {
    filter: none;
}
""".strip()

CSS = """
    #chat-box {
        height: 1fr;
        border: none;
        padding: 1;
        scrollbar-size: 2 1;
        scrollbar-color: #808080;
    }
    TextArea {
        height: 1fr;
        border: none;
        scrollbar-size: 0 0;
        background: transparent;
    }
    #input-box {
        height: 6;
        border: solid #334466;
        padding: 0;
    }
    TextArea .text-area--cursor-line {
        background: transparent;
    }
    #status-box {
        height: 1;
        border: none;
        padding: 0 0 0 1;
    }

    #logo-overlay {
        height: 100%;
        align: center middle;
    }
    #logo-overlay > Static {
        width: auto;
    }

    .bubble {
        border: none;
        margin: 0 3 0 0;
        height: auto;
        padding: 1;
    }
    Collapsible.bubble > CollapsibleTitle {
        padding: 0 1;
    }
    .bubble *:focus,
    .bubble *:hover {
        background-tint: transparent;
    }
    CollapsibleTitle:focus {
        background: transparent;
    }

    .user-bubble, .reply-bubble {
        padding: 1 1 1 1;
    }
    .reply-bubble {
        padding: 1 1 0 1;
    }
    .reply-bubble MarkdownParagraph {
        margin: 0 0 1 0;
    }
    .reply-bubble MarkdownBlock:last-child {
        margin-bottom: 0;
    }
    .user-bubble {
        background: #1A1A1A;
    }
    .thinking-bubble, .tool-bubble {
        background: transparent;
        padding: 1 1 0 1;
    }
    .thinking-bubble > CollapsibleTitle {
        color: #5B9BD5;
    }
    .tool-bubble > CollapsibleTitle {
        color: #70AD47;
    }
    .thinking-bubble > Contents > Static {
        color: #9B9B9B;
    }
    .tool-error > CollapsibleTitle {
        color: #FF5555;
    }
    .summary-bubble {
        height: 1;
        margin: 1 0 1 0;
        padding: 0 1 0 1;
    }

    .reply-bubble > Markdown,
    .tool-bubble > Contents > Markdown {
        background: transparent;
    }
    .reply-bubble MarkdownParagraph {
        margin: 0 0 1 0;
    }
    .reply-bubble MarkdownTable {
        margin: 0 0 1 0;
    }
    .reply-bubble MarkdownBlockQuote {
        margin: 0 0 1 0;
    }
    .reply-bubble MarkdownFence,
    .tool-bubble MarkdownFence {
        margin: 0 0 1 0;
        background: #1A1A1A;
    }
    .reply-bubble MarkdownList,
    .reply-bubble MarkdownListItem {
        margin: 0;
    }
    .reply-bubble MarkdownH1,
    .reply-bubble MarkdownH2,
    .reply-bubble MarkdownH3,
    .reply-bubble MarkdownH4,
    .reply-bubble MarkdownH5,
    .reply-bubble MarkdownH6 {
        margin: 1 0;
    }
"""

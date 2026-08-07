CSS = """
    Screen {
        background: #0c0c0c;
        & > .screen--selection {
            background: $primary;
            color: $text;
        }
    }
    #chat-box {
        height: 1fr;
        border: none;
        padding: 1 1 0 2;
        scrollbar-size: 2 1;
        scrollbar-color: #808080;
    }
    CommandPalette {
        layer: overlay;
        dock: bottom;
        width: 100%;
        height: auto;
        max-height: 8;
        margin: 0 0 7 0;
        border: none;
    }
    SessionPicker,
    ProviderPicker,
    ModelPicker,
    StrengthPicker,
    McpPicker {
        display: none;
        layer: overlay;
        width: 45%;
        height: 45%;
        background: #1A1A1A;
        border: none;
        padding: 1;
    }
    SessionPicker.visible,
    ProviderPicker.visible,
    ModelPicker.visible,
    StrengthPicker.visible,
    McpPicker.visible {
        display: block;
    }
    SessionPicker #picker-search,
    ProviderPicker #picker-search,
    ModelPicker #picker-search,
    StrengthPicker #picker-search,
    McpPicker #picker-search {
        height: 1;
        border: none;
        background: #222222;
        margin-bottom: 1;
    }
    SessionPicker #picker-list,
    ProviderPicker #picker-list,
    ModelPicker #picker-list,
    StrengthPicker #picker-list,
    McpPicker #picker-list {
        height: 1fr;
        border: none;
        padding: 0;
        scrollbar-size: 1 1;
    }
    SessionPicker #picker-footer,
    ProviderPicker #picker-footer,
    ModelPicker #picker-footer,
    StrengthPicker #picker-footer,
    McpPicker #picker-footer {
        height: 1;
        padding: 0;
        content-align: left bottom;
        color: #888888;
    }
    SessionPicker .picker-row,
    ProviderPicker .picker-row,
    ModelPicker .picker-row,
    StrengthPicker .picker-row,
    McpPicker .picker-row {
        height: 1;
        padding: 0 1;
    }
    ModelPicker .picker-row {
        layout: horizontal;
        width: 100%;
    }
    ModelPicker .model-name {
        width: 1fr;
        height: 1;
        overflow: hidden;
    }
    ModelPicker .model-provider {
        width: auto;
        height: 1;
        text-align: right;
        color: #555555;
    }
    SessionPicker .picker-row.selected,
    ProviderPicker .picker-row.selected,
    ModelPicker .picker-row.selected,
    StrengthPicker .picker-row.selected,
    McpPicker .picker-row.selected {
        background: #334466;
    }
    McpPicker .picker-row {
        layout: horizontal;
        width: 100%;
    }
    McpPicker .mcp-name {
        width: 1fr;
        height: 1;
        overflow: hidden;
    }
    McpPicker .mcp-status {
        width: auto;
        height: 1;
        text-align: right;
        padding-left: 1;
    }
    ProviderKeyDialog {
        display: none;
        layer: overlay;
        width: 45%;
        height: auto;
        background: #1A1A1A;
        border: none;
        padding: 1;
    }
    ProviderKeyDialog.visible {
        display: block;
    }
    ProviderKeyDialog #dialog-title {
        height: 1;
        margin-bottom: 1;
        color: #888888;
    }
    ProviderKeyDialog Input {
        height: 1;
        border: none;
        background: #222222;
        margin-bottom: 1;
    }
    ProviderKeyDialog #custom-name,
    ProviderKeyDialog #custom-url {
        display: none;
    }
    ProviderKeyDialog #dialog-error {
        height: 1;
        margin-bottom: 1;
        color: #FF5555;
    }
    ProviderKeyDialog #dialog-footer {
        height: 1;
        padding: 0;
        color: #888888;
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
    #input-status {
        height: 1;
        padding: 0 1;
    }
    TextArea .text-area--cursor-line {
        background: transparent;
    }
    TextArea .text-area--placeholder {
        color: #FF5555;
    }
    #status-box {
        height: 1;
        border: none;
        padding: 0 0 0 1;
    }
    #status-box #status {
        color: #666666;
    }

    #logo-overlay {
        height: 100%;
        align: center middle;
    }
    #logo-overlay > Static {
        width: auto;
    }
"""

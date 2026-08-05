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
        padding: 1 1 1 2;
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
    ModelPicker {
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
    ModelPicker.visible {
        display: block;
    }
    SessionPicker #picker-search,
    ProviderPicker #picker-search,
    ModelPicker #picker-search {
        height: 1;
        border: none;
        background: #222222;
        margin-bottom: 1;
    }
    SessionPicker #picker-list,
    ProviderPicker #picker-list,
    ModelPicker #picker-list {
        height: 1fr;
        border: none;
        padding: 0;
        scrollbar-size: 1 1;
    }
    SessionPicker #picker-footer,
    ProviderPicker #picker-footer,
    ModelPicker #picker-footer {
        height: 1;
        padding: 0;
        content-align: left bottom;
        color: #888888;
    }
    SessionPicker .picker-row,
    ProviderPicker .picker-row,
    ModelPicker .picker-row {
        height: 1;
        padding: 0 1;
    }
    SessionPicker .picker-row.selected,
    ProviderPicker .picker-row.selected,
    ModelPicker .picker-row.selected {
        background: #334466;
    }
    ProviderKeyDialog,
    ExaKeyDialog {
        display: none;
        layer: overlay;
        width: 45%;
        height: auto;
        background: #1A1A1A;
        border: none;
        padding: 1;
    }
    ProviderKeyDialog.visible,
    ExaKeyDialog.visible {
        display: block;
    }
    ProviderKeyDialog #dialog-title,
    ExaKeyDialog #dialog-title {
        height: 1;
        margin-bottom: 1;
        color: #888888;
    }
    ProviderKeyDialog Input,
    ExaKeyDialog Input {
        height: 1;
        border: none;
        background: #222222;
        margin-bottom: 1;
    }
    ProviderKeyDialog #custom-name,
    ProviderKeyDialog #custom-url {
        display: none;
    }
    ProviderKeyDialog #dialog-error,
    ExaKeyDialog #dialog-error {
        height: 1;
        margin-bottom: 1;
        color: #FF5555;
    }
    ProviderKeyDialog #dialog-footer,
    ExaKeyDialog #dialog-footer {
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

    #logo-overlay {
        height: 100%;
        align: center middle;
    }
    #logo-overlay > Static {
        width: auto;
    }
"""

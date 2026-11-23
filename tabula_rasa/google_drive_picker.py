"""Google Picker page for explicit folder and workbook authorization."""

from __future__ import annotations

import json


_PICKER_SCRIPT = """
const data = JSON.parse(document.querySelector('#picker-data').textContent);
const choose = document.querySelector('#choose');
const cancel = document.querySelector('#cancel');
const status = document.querySelector('#status');

async function finish(id) {
    choose.disabled = cancel.disabled = true;
    try {
        const response = await fetch(window.location.pathname, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(id)
        });
        if (!response.ok) throw new Error();
        status.textContent = await response.text();
    } catch {
        status.textContent = 'Picker could not contact the command. Check the terminal.';
    }
}

cancel.onclick = () => finish(null);
choose.onclick = () => {
    choose.disabled = true;
    status.textContent = 'Opening Google Picker…';
    function failed() {
        status.textContent = 'Google Picker failed to load. Retry or cancel.';
        choose.disabled = false;
    }
    try {
        gapi.load('picker', {callback: () => {
            try {
                const sheets = data.kind === 'spreadsheet';
                const view = new google.picker.DocsView(sheets
                    ? google.picker.ViewId.SPREADSHEETS : google.picker.ViewId.FOLDERS);
                view.setIncludeFolders(!sheets);
                view.setSelectFolderEnabled(!sheets);
                view.setMimeTypes(sheets ? 'application/vnd.google-apps.spreadsheet'
                    : 'application/vnd.google-apps.folder');
                new google.picker.PickerBuilder()
                    .setOAuthToken(data.accessToken)
                    .setDeveloperKey(data.apiKey)
                    .setAppId(data.appId)
                    .setOrigin(window.location.origin)
                    .addView(view)
                    .setCallback((result) => {
                        if (result.action === google.picker.Action.PICKED) {
                            const selection = result[google.picker.Response.DOCUMENTS][0];
                            finish(selection[google.picker.Document.ID]);
                        } else if (result.action === google.picker.Action.CANCEL) {
                            finish(null);
                        }
                    })
                    .build().setVisible(true);
            } catch {
                failed();
            }
        }, onerror: failed, timeout: 10000, ontimeout: failed});
    } catch {
        failed();
    }
};
"""


def picker_page(
    kind: str, access_token: str, api_key: str, project_number: str
) -> bytes:
    data = json.dumps(
        {
            "kind": kind,
            "accessToken": access_token,
            "apiKey": api_key,
            "appId": project_number,
        }
    ).replace("<", r"\u003c")
    label = "workbook" if kind == "spreadsheet" else "Drive folder"
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Choose a Google {label}</title>
<script src="https://apis.google.com/js/api.js" defer></script>
<h1>Choose a Google {label}</h1>
<button type="button" id="choose">Open Google Picker</button>
<button type="button" id="cancel">Cancel</button>
<p id="status" role="status"></p>
<script type="application/json" id="picker-data">{data}</script>
<script>{_PICKER_SCRIPT}</script>
</html>
""".encode()

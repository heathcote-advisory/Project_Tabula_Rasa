type SelectedFolder = { id: string; name: string };
type PickerResponse = { action: string; docs?: SelectedFolder[] };
type PickerBuilder = {
  addView: (view: object) => PickerBuilder;
  setAppId: (id: string) => PickerBuilder;
  setDeveloperKey: (key: string) => PickerBuilder;
  setOAuthToken: (token: string) => PickerBuilder;
  setOrigin: (origin: string) => PickerBuilder;
  setCallback: (callback: (data: PickerResponse) => void) => PickerBuilder;
  build: () => { setVisible: (visible: boolean) => void; dispose: () => void };
};
type PickerApi = {
  DocsView: new (id: string) => {
    setIncludeFolders: (include: boolean) => void;
    setMode: (mode: string) => void;
    setMimeTypes: (types: string) => void;
    setSelectFolderEnabled: (select: boolean) => void;
  };
  ViewId: { FOLDERS: string };
  DocsViewMode: { LIST: string };
  PickerBuilder: new () => PickerBuilder;
};
const browser = window as Window & {
  gapi?: { load: (name: string, options: {
    callback: () => void; onerror: () => void; ontimeout: () => void; timeout: number;
  }) => void };
  google?: { picker: PickerApi };
};
let loading: Promise<PickerApi> | undefined;

function loadPicker() {
  loading ??= new Promise<PickerApi>((resolve, reject) => {
    const fail = () => reject(new Error('Google Picker unavailable'));
    const load = () => {
      if (!browser.gapi) return fail();
      browser.gapi.load('picker', { callback: () => browser.google?.picker
        ? resolve(browser.google.picker) : fail(), onerror: fail, ontimeout: fail, timeout: 10000 });
    };
    if (browser.gapi) {
      load();
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://apis.google.com/js/api.js';
    const timer = window.setTimeout(() => { script.remove(); fail(); }, 10000);
    script.onload = () => { window.clearTimeout(timer); load(); };
    script.onerror = () => { window.clearTimeout(timer); script.remove(); fail(); };
    document.head.append(script);
  }).catch((error: unknown) => {
    loading = undefined;
    throw error;
  });
  return loading;
}

export async function pickDriveFolder(token: string, key: string, project: string, signal: AbortSignal) {
  const api = await loadPicker();
  if (signal.aborted) return;
  const view = new api.DocsView(api.ViewId.FOLDERS);
  view.setMode(api.DocsViewMode.LIST);
  view.setIncludeFolders(true);
  view.setMimeTypes('application/vnd.google-apps.folder');
  view.setSelectFolderEnabled(true);

  return new Promise<SelectedFolder | undefined>((resolve, reject) => {
    const picker = new api.PickerBuilder().addView(view).setAppId(project).setDeveloperKey(key)
      .setOAuthToken(token).setOrigin(window.location.origin).setCallback(data => {
        if (!['picked', 'cancel', 'error'].includes(data.action)) return;
        dispose();
        if (data.action === 'error') reject(new Error('Google Picker unavailable'));
        else resolve(data.action === 'picked' ? data.docs?.[0] : undefined);
      }).build();
    const cancel = () => { dispose(); resolve(undefined); };
    const dispose = () => {
      signal.removeEventListener('abort', cancel);
      picker.dispose();
    };
    signal.addEventListener('abort', cancel, { once: true });
    picker.setVisible(true);
  });
}

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const POLL_INTERVAL_SECONDS = 900;
const REPO_ROOT_HINT_FILE = '.repo-root';
const LIVE_FALLBACK_SOURCES = new Map([
    ['claude', new Set(['claude-auth-metadata'])],
    ['gemini', new Set(['gemini-startup', 'gemini-auth-metadata'])],
]);

const UsageIndicator = GObject.registerClass(
class UsageIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.0, 'AI Energy Bar');

        this._extension = extension;
        this._refreshInFlight = false;
        this._pollSourceId = 0;
        this._providerRows = new Map();
        this._lastSnapshot = null;

        this.add_style_class_name('panel-button');

        const box = new St.BoxLayout({
            style_class: 'panel-status-menu-box',
            x_expand: true,
        });

        this._label = new St.Label({
            text: 'AI …',
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'ai-energy-label',
        });

        box.add_child(this._label);
        this.add_child(box);

        this._buildMenu();
        this._schedulePolling();
        this._refresh();
    }

    _buildMenu() {
        this.menu.removeAll();
        this._providerRows.clear();

        this._headerItem = this._createHeaderItem();
        this.menu.addMenuItem(this._headerItem);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        for (const key of ['claude', 'codex', 'gemini']) {
            const item = this._createProviderItem(key);
            this._providerRows.set(key, item);
            this.menu.addMenuItem(item.container);
        }

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const refreshItem = new PopupMenu.PopupMenuItem('Refresh Now');
        refreshItem.connect('activate', () => {
            this._refresh();
        });
        this.menu.addMenuItem(refreshItem);
    }

    _createHeaderItem() {
        const item = new PopupMenu.PopupBaseMenuItem({
            reactive: false,
            can_focus: false,
        });

        this._headerLabel = new St.Label({
            text: 'Waiting for local probe…',
            x_expand: true,
            style_class: 'ai-energy-header',
        });

        item.add_child(this._headerLabel);
        return item;
    }

    _createProviderItem(key) {
        const item = new PopupMenu.PopupBaseMenuItem({
            reactive: false,
            can_focus: false,
        });

        const box = new St.BoxLayout({
            vertical: true,
            x_expand: true,
            style_class: 'ai-energy-provider-box',
        });

        const title = new St.Label({
            text: key,
            x_expand: true,
            style_class: 'ai-energy-provider-title',
        });

        const summary = new St.Label({
            text: 'Loading…',
            x_expand: true,
            style_class: 'ai-energy-provider-summary',
        });
        summary.clutter_text.line_wrap = true;
        summary.clutter_text.ellipsize = 0;

        const detail = new St.Label({
            text: '',
            x_expand: true,
            style_class: 'ai-energy-provider-detail',
        });
        detail.clutter_text.line_wrap = true;
        detail.clutter_text.ellipsize = 0;

        const warning = new St.Label({
            text: '',
            x_expand: true,
            style_class: 'ai-energy-provider-warning',
        });
        warning.clutter_text.line_wrap = true;
        warning.clutter_text.ellipsize = 0;

        box.add_child(title);
        box.add_child(summary);
        box.add_child(detail);
        box.add_child(warning);
        item.add_child(box);

        return {container: item, title, summary, detail, warning};
    }

    _schedulePolling() {
        this._pollSourceId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT,
            POLL_INTERVAL_SECONDS,
            () => {
                this._refresh();
                return GLib.SOURCE_CONTINUE;
            });
    }

    _clearPolling() {
        if (this._pollSourceId !== 0) {
            GLib.Source.remove(this._pollSourceId);
            this._pollSourceId = 0;
        }
    }

    _refresh() {
        if (this._refreshInFlight)
            return;

        this._refreshInFlight = true;
        this._headerLabel.text = 'Refreshing local CLI status…';

        const python = this._resolvePython();
        if (!python) {
            this._setErrorState(
                'No usable Python interpreter found. Run `uv sync --group dev` or install `python3`.',
            );
            return;
        }

        const probePath = GLib.build_filenamev([this._extension.path, 'probe.py']);
        const process = Gio.Subprocess.new(
            [python, probePath],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
        );

        process.communicate_utf8_async(null, null, (_process, result) => {
            try {
                const [, stdout, stderr] = process.communicate_utf8_finish(result);

                if (!process.get_successful()) {
                    this._setErrorState(stderr?.trim() || 'Probe command failed.');
                    return;
                }

                const snapshot = this._mergeWithLastSnapshot(JSON.parse(stdout));
                this._lastSnapshot = snapshot;
                this._renderSnapshot(snapshot);
            } catch (error) {
                this._setErrorState(`${error}`);
            } finally {
                this._refreshInFlight = false;
            }
        });
    }

    _resolvePython() {
        const candidates = [];
        const repoRoot = this._readRepoRootHint();

        if (repoRoot) {
            candidates.push(GLib.build_filenamev([repoRoot, '.venv', 'bin', 'python3']));
            candidates.push(GLib.build_filenamev([repoRoot, '.venv', 'bin', 'python']));
        }

        candidates.push(GLib.find_program_in_path('python3'));

        for (const candidate of candidates) {
            if (candidate && GLib.file_test(candidate, GLib.FileTest.IS_EXECUTABLE))
                return candidate;
        }

        return null;
    }

    _readRepoRootHint() {
        const hintPath = GLib.build_filenamev([this._extension.path, REPO_ROOT_HINT_FILE]);
        const hintFile = Gio.File.new_for_path(hintPath);
        if (!hintFile.query_exists(null))
            return null;

        try {
            const [, contents] = hintFile.load_contents(null);
            return new TextDecoder().decode(contents).trim() || null;
        } catch (error) {
            logError(error, 'Failed to read AI Energy Bar repo root hint');
            return null;
        }
    }

    _setErrorState(message) {
        this._refreshInFlight = false;
        this._label.text = 'AI error';
        this._headerLabel.text = `Probe error: ${message}`;

        for (const provider of this._providerRows.values()) {
            provider.summary.text = 'Unavailable';
            provider.detail.text = message;
            provider.detail.visible = true;
            provider.warning.text = '';
            provider.warning.visible = false;
        }
    }

    _mergeWithLastSnapshot(snapshot) {
        if (!this._lastSnapshot?.providers)
            return snapshot;

        const mergedProviders = {...(snapshot.providers || {})};
        for (const [key, fallbackSources] of LIVE_FALLBACK_SOURCES.entries()) {
            const currentProvider = mergedProviders[key];
            const previousProvider = this._lastSnapshot.providers[key];
            if (!currentProvider || !previousProvider)
                continue;
            if (!fallbackSources.has(currentProvider.source))
                continue;
            if (fallbackSources.has(previousProvider.source))
                continue;

            mergedProviders[key] = {
                ...previousProvider,
                warning: `Showing the last live reading. Current poll fell back at ${snapshot.generated_at}.`,
            };
        }

        return {
            ...snapshot,
            providers: mergedProviders,
        };
    }

    _renderSnapshot(snapshot) {
        const generatedAt = snapshot.generated_at || 'unknown';
        const providers = snapshot.providers || {};
        const compactLabels = [];

        this._headerLabel.text = `Updated ${generatedAt}`;

        for (const key of ['claude', 'codex', 'gemini']) {
            const provider = providers[key] || {};
            const row = this._providerRows.get(key);
            if (!row)
                continue;

            const titleParts = [provider.display_name || key];
            if (provider.version)
                titleParts.push(`v${provider.version}`);
            row.title.text = titleParts.join('  ·  ');
            row.summary.text = provider.summary || 'No status available';
            row.summary.visible = Boolean(provider.summary);
            row.detail.text = provider.detail || '';
            row.detail.visible = Boolean(provider.detail);
            row.warning.text = provider.warning || '';
            row.warning.visible = Boolean(provider.warning);

            if (provider.compact)
                compactLabels.push(provider.compact);
        }

        this._label.text = compactLabels.length > 0
            ? compactLabels.join('  ')
            : 'AI --';
    }

    destroy() {
        this._clearPolling();
        super.destroy();
    }
});

export default class AiEnergyBarExtension extends Extension {
    enable() {
        this._indicator = new UsageIndicator(this);
        Main.panel.addToStatusArea(this.uuid, this._indicator, 0, 'right');
    }

    disable() {
        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
    }
}

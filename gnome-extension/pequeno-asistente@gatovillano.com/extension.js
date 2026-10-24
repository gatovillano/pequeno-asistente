import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import St from 'gi://St';

import { Extension, gettext as _ } from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const WM_CLASS = 'com.gatovillano.pequenoasistente';
const SOCKET_NAME = 'pequeno-asistente/asistente.sock';

const AssistantIndicator = GObject.registerClass(
class AssistantIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.0, _('Pequeño Asistente'), false);
        this._extension = extension;

        this.add_style_class_name('pequeno-asistente-indicator');

        // Setup Icon
        this._icon = this._createIcon();
        this.add_child(this._icon);

        // Disconnect default menu toggle on primary click
        this._clickGesture.set_enabled(false);

        // Primary click (Left click): Toggle assistant dropdown window
        this._primaryGesture = new Clutter.ClickGesture({
            required_button: Clutter.BUTTON_PRIMARY,
            recognize_on_press: true,
        });
        this._primaryGesture.connect('recognize', () => {
            this._extension.toggleAssistant();
        });
        this.add_action(this._primaryGesture);

        // Secondary click (Right click): Open popup menu
        this._secondaryGesture = new Clutter.ClickGesture({
            required_button: Clutter.BUTTON_SECONDARY,
            recognize_on_press: true,
        });
        this._secondaryGesture.connect('recognize', () => {
            this.menu?.toggle();
        });
        this.add_action(this._secondaryGesture);

        // Build popup menu
        this._buildMenu();
    }

    _createIcon() {
        const extPath = this._extension.path;
        const symbolicPath = `${extPath}/icons/pequeno-asistente-symbolic.svg`;
        const colorIconPath = `${extPath}/icons/icon.svg`;

        let gicon = null;
        if (GLib.file_test(symbolicPath, GLib.FileTest.EXISTS)) {
            gicon = Gio.FileIcon.new(Gio.File.new_for_path(symbolicPath));
        } else if (GLib.file_test(colorIconPath, GLib.FileTest.EXISTS)) {
            gicon = Gio.FileIcon.new(Gio.File.new_for_path(colorIconPath));
        }

        if (gicon) {
            return new St.Icon({
                gicon: gicon,
                style_class: 'system-status-icon pequeno-asistente-icon',
            });
        }

        return new St.Icon({
            icon_name: 'face-smile-symbolic',
            style_class: 'system-status-icon',
        });
    }

    _buildMenu() {
        // 1. Header / Status item
        this._statusItem = new PopupMenu.PopupMenuItem('🤖 Pequeño Asistente', {
            reactive: false,
            style_class: 'pequeno-asistente-header',
        });
        this.menu.addMenuItem(this._statusItem);

        // 2. Toggle item
        const toggleItem = new PopupMenu.PopupMenuItem(_('✨ Desplegar / Ocultar ventana'));
        toggleItem.connect('activate', () => {
            this._extension.toggleAssistant();
        });
        this.menu.addMenuItem(toggleItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // 3. Settings item
        const settingsItem = new PopupMenu.PopupMenuItem(_('⚙️ Configuración (Modelos & MCPs)'));
        settingsItem.connect('activate', () => {
            this._extension.openSettings();
        });
        this.menu.addMenuItem(settingsItem);

        // 4. Clear chat item
        const clearItem = new PopupMenu.PopupMenuItem(_('🧹 Limpiar conversación'));
        clearItem.connect('activate', () => {
            this._extension.clearChat();
        });
        this.menu.addMenuItem(clearItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // 5. Restart item
        const restartItem = new PopupMenu.PopupMenuItem(_('🔄 Reiniciar Asistente'));
        restartItem.connect('activate', () => {
            this._extension.restartAssistant();
        });
        this.menu.addMenuItem(restartItem);

        // 6. Quit item
        const quitItem = new PopupMenu.PopupMenuItem(_('❌ Salir'));
        quitItem.connect('activate', () => {
            this._extension.quitAssistant();
        });
        this.menu.addMenuItem(quitItem);
    }

    updateStatus(isRunning) {
        if (this._statusItem) {
            const label = this._statusItem.label;
            if (label) {
                label.text = isRunning
                    ? '🤖 Pequeño Asistente  •  🟢 Activo'
                    : '🤖 Pequeño Asistente  •  ⚪ Inactivo';
            }
        }
        if (this._icon) {
            this._icon.opacity = isRunning ? 255 : 160;
        }
    }
});

export default class PequenoAsistenteExtension extends Extension {
    enable() {
        this._indicator = new AssistantIndicator(this);
        Main.panel.addToStatusArea(this.uuid, this._indicator, 0, 'right');

        // Hook window creation to position it dropdown style
        this._windowCreatedId = global.display.connect('window-created', (_disp, metaWindow) => {
            const wmClass = metaWindow.get_wm_class ? metaWindow.get_wm_class() : '';
            if (wmClass === WM_CLASS) {
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 150, () => {
                    this.positionWindow(metaWindow);
                    return GLib.SOURCE_REMOVE;
                });
            }
        });

        // Periodic status checker
        this._checkStatus();
        this._pollTimerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 4, () => {
            this._checkStatus();
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        if (this._pollTimerId) {
            GLib.source_remove(this._pollTimerId);
            this._pollTimerId = null;
        }

        if (this._windowCreatedId) {
            global.display.disconnect(this._windowCreatedId);
            this._windowCreatedId = null;
        }

        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
    }

    _getSocketPath() {
        return `${GLib.get_home_dir()}/.config/${SOCKET_NAME}`;
    }

    async _sendCommand(cmd) {
        try {
            const socketPath = this._getSocketPath();
            const socketFile = Gio.File.new_for_path(socketPath);
            if (!socketFile.query_exists(null)) {
                return false;
            }

            const addr = Gio.UnixSocketAddress.new(socketPath);
            const client = new Gio.SocketClient();
            const conn = await client.connect_async(addr, null);

            const outStream = conn.get_output_stream();
            outStream.write_all(`${cmd}\n`, null);

            const inStream = new Gio.DataInputStream({
                base_stream: conn.get_input_stream(),
            });
            const [line] = inStream.read_line_utf8(null);
            conn.close(null);

            return line !== null;
        } catch (_err) {
            return false;
        }
    }

    async _checkStatus() {
        const isRunning = await this._sendCommand('ping');
        if (this._indicator) {
            this._indicator.updateStatus(isRunning);
        }
        return isRunning;
    }

    _launchAssistant() {
        try {
            const home = GLib.get_home_dir();
            const binWrapper = `${home}/.local/bin/pequeno-asistente`;
            const projectScript = `${home}/Proyectos/Pequeño asistente/scripts/toggle.sh`;

            let target = binWrapper;
            if (!GLib.file_test(binWrapper, GLib.FileTest.IS_EXECUTABLE)) {
                target = projectScript;
            }

            Gio.Subprocess.new(['/bin/bash', target], Gio.SubprocessFlags.NONE);
        } catch (err) {
            console.error(`[Pequeño Asistente] Failed to launch: ${err.message}`);
        }
    }

    findAssistantWindow() {
        const windows = global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null);
        for (const win of windows) {
            const wmClass = win.get_wm_class ? win.get_wm_class() : '';
            const title = win.get_title ? win.get_title() : '';
            if (wmClass === WM_CLASS || (title && title.includes('Pequeño Asistente'))) {
                return win;
            }
        }
        return null;
    }

    positionWindow(win) {
        if (!win) return;

        try {
            // Keep window floating above other windows
            if (!win.is_above()) {
                win.make_above();
            }

            // Pin to all workspaces (like top-bar dropdown widget)
            if (!win.is_on_all_workspaces()) {
                win.stick();
            }

            // Position at top-right, just below panel
            const monitor = Main.layoutManager.primaryMonitor;
            const frameRect = win.get_frame_rect();
            const panelHeight = Main.panel.height;

            const targetX = Math.round(monitor.x + monitor.width - frameRect.width - 20);
            const targetY = Math.round(monitor.y + panelHeight + 8);

            win.move_frame(true, targetX, targetY);

            // Activate and focus window without shell notification
            win.activate(global.get_current_time());
        } catch (err) {
            console.warn(`[Pequeño Asistente] Could not position window: ${err}`);
        }
    }

    async toggleAssistant() {
        const sent = await this._sendCommand('toggle');
        if (!sent) {
            // Not running yet, launch it!
            this._launchAssistant();
        }

        // Reposition and focus the window if it is shown
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 120, () => {
            const win = this.findAssistantWindow();
            if (win) {
                this.positionWindow(win);
            }
            this._checkStatus();
            return GLib.SOURCE_REMOVE;
        });
    }

    async openSettings() {
        const sent = await this._sendCommand('settings');
        if (!sent) {
            this._launchAssistant();
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 800, () => {
                this._sendCommand('settings');
                return GLib.SOURCE_REMOVE;
            });
        } else {
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 100, () => {
                const win = this.findAssistantWindow();
                if (win) this.positionWindow(win);
                return GLib.SOURCE_REMOVE;
            });
        }
    }

    async clearChat() {
        await this._sendCommand('clear');
    }

    async restartAssistant() {
        try {
            // Kill existing process
            Gio.Subprocess.new(
                ['/usr/bin/pkill', '-f', 'pequeno_asistente/main.py'],
                Gio.SubprocessFlags.NONE
            );

            // Give it 500ms to terminate, then restart
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500, () => {
                this._launchAssistant();
                this._checkStatus();
                return GLib.SOURCE_REMOVE;
            });
        } catch (err) {
            console.error(`[Pequeño Asistente] Error restarting: ${err.message}`);
        }
    }

    async quitAssistant() {
        try {
            Gio.Subprocess.new(
                ['/usr/bin/pkill', '-f', 'pequeno_asistente/main.py'],
                Gio.SubprocessFlags.NONE
            );
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
                this._checkStatus();
                return GLib.SOURCE_REMOVE;
            });
        } catch (err) {
            console.error(`[Pequeño Asistente] Error quitting: ${err.message}`);
        }
    }
}

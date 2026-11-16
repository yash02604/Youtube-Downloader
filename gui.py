import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import logging
import os
import queue
import subprocess
import threading
import uuid
from PIL import ImageTk
from download_manager import DownloadManager, DownloadPriority
from error_handler import UserFriendlyError
from format_selector import FormatSelector
from js_runtime import discover_deno
from video_analyzer import VideoAnalyzer
from config import config_manager
from logging_utils import configure_application_logging
from thumbnail_preview import fetch_thumbnail_image


class IDMCloneGUI:
    """Main GUI application for IDM Clone"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("IDM Clone - Internet Download Manager")
        self.root.geometry("1050x620")
        self.root.minsize(920, 520)
        
        self.download_manager = DownloadManager(
            max_concurrent_downloads=config_manager.get("max_concurrent_downloads", 3)
        )
        self.ui_refresh_queue = queue.Queue(maxsize=1)
        self.video_info_queue = queue.Queue()
        self.thumbnail_preview_queue = queue.Queue()
        self.thumbnail_previews_in_flight = set()
        self.video_inspection_pending = False
        self.download_manager.register_progress_callback(self.on_progress_update)
        self.download_manager.register_status_callback(self.on_status_update)
        
        self.configure_style()
        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close_application)
        self.refresh_ffmpeg_status()
        self.update_interval = 250  # Faster UI updates for realtime speed
        self.update_ui()

    def configure_style(self):
        """Configure cohesive dark theme styles"""
        self.colors = {
            'bg': '#11121a',
            'panel': '#1c1f2b',
            'card': '#24283b',
            'accent': '#58f5e2',
            'accent_hover': '#72fff1',
            'text': '#f4f7ff',
            'muted': '#8e94ad'
        }
        style = ttk.Style()
        style.theme_use('clam')
        self.root.configure(bg=self.colors['bg'])
        
        style.configure("Dark.TFrame", background=self.colors['panel'])
        style.configure("Card.TFrame", background=self.colors['card'], relief='flat', borderwidth=0)
        style.configure("Dark.TLabel", background=self.colors['panel'], foreground=self.colors['text'])
        style.configure("Card.TLabel", background=self.colors['card'], foreground=self.colors['text'])
        style.configure("Muted.TLabel", background=self.colors['panel'], foreground=self.colors['muted'])
        style.configure("Accent.TButton",
                        background=self.colors['accent'],
                        foreground='#0d101b',
                        borderwidth=0,
                        focusthickness=0,
                        padding=6)
        style.map("Accent.TButton",
                  background=[('pressed', self.colors['accent_hover']),
                              ('active', self.colors['accent_hover'])])
        style.configure("Toolbar.TButton",
                        background=self.colors['panel'],
                        foreground=self.colors['text'],
                        borderwidth=0,
                        padding=6)
        style.map("Toolbar.TButton",
                  background=[('pressed', '#3c3f54'), ('active', '#3c3f54')])
        style.configure("Dark.Treeview",
                        background=self.colors['panel'],
                        fieldbackground=self.colors['panel'],
                        foreground=self.colors['text'],
                        rowheight=26)
        style.configure("Dark.Treeview.Heading",
                        background=self.colors['card'],
                        foreground=self.colors['text'],
                        relief='flat')
        style.map("Dark.Treeview.Heading",
                  background=[('active', self.colors['accent'])])
        self.style = style
        
    def setup_ui(self):
        """Setup the user interface"""
        # Main container
        main_frame = ttk.Frame(self.root, style="Dark.TFrame", padding="12")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)
        
        # Toolbar
        toolbar = ttk.Frame(main_frame, style="Dark.TFrame")
        toolbar.grid(row=0, column=0, sticky=(tk.W, tk.E))
        for i in range(8):
            toolbar.columnconfigure(i, weight=1)
        
        ttk.Button(toolbar, text="Add URL", style="Accent.TButton", command=self.add_download).grid(row=0, column=0, padx=4, pady=4, sticky=tk.EW)
        ttk.Button(toolbar, text="Resume", style="Toolbar.TButton", command=self.resume_selected).grid(row=0, column=1, padx=4, pady=4, sticky=tk.EW)
        ttk.Button(toolbar, text="Pause", style="Toolbar.TButton", command=self.pause_selected).grid(row=0, column=2, padx=4, pady=4, sticky=tk.EW)
        ttk.Button(toolbar, text="Cancel", style="Toolbar.TButton", command=self.cancel_selected).grid(row=0, column=3, padx=4, pady=4, sticky=tk.EW)
        ttk.Button(toolbar, text="Remove entry", style="Toolbar.TButton", command=self.remove_selected).grid(row=0, column=4, padx=4, pady=4, sticky=tk.EW)
        ttk.Button(toolbar, text="Clear Completed", style="Toolbar.TButton", command=self.clear_completed).grid(row=0, column=5, padx=4, pady=4, sticky=tk.EW)
        ttk.Button(toolbar, text="Configure FFmpeg", style="Toolbar.TButton", command=self.configure_ffmpeg).grid(row=0, column=6, padx=4, pady=4, sticky=tk.EW)
        ttk.Button(toolbar, text="Settings / Diagnostics", style="Toolbar.TButton", command=self.show_settings).grid(row=0, column=7, padx=4, pady=4, sticky=tk.EW)
        
        # Input card
        input_card = ttk.Frame(main_frame, style="Card.TFrame", padding="12")
        input_card.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(8, 12))
        input_card.columnconfigure(1, weight=1)
        
        ttk.Label(input_card, text="Download URL", style="Card.TLabel").grid(row=0, column=0, sticky=tk.W, pady=(0, 4))
        self.url_entry = ttk.Entry(input_card)
        self.url_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(10, 0))
        self.url_entry.bind('<Return>', lambda e: self.add_download())
        
        ttk.Label(input_card, text="Save to", style="Card.TLabel").grid(row=1, column=0, sticky=tk.W, pady=(8, 4))
        path_frame = ttk.Frame(input_card, style="Card.TFrame")
        path_frame.grid(row=1, column=1, sticky=(tk.W, tk.E))
        path_frame.columnconfigure(0, weight=1)
        self.location_var = tk.StringVar(value=config_manager.get("default_download_path"))
        location_entry = ttk.Entry(path_frame, textvariable=self.location_var)
        location_entry.grid(row=0, column=0, sticky=(tk.W, tk.E))
        ttk.Button(path_frame, text="Browse", style="Toolbar.TButton", command=self.browse_location).grid(row=0, column=1, padx=(8, 0))
        
        ttk.Label(input_card, text="File name (optional)", style="Card.TLabel").grid(row=2, column=0, sticky=tk.W, pady=(8, 4))
        self.filename_var = tk.StringVar()
        filename_entry = ttk.Entry(input_card, textvariable=self.filename_var)
        filename_entry.grid(row=2, column=1, sticky=(tk.W, tk.E))
        
        # Content area with categories + downloads
        content_frame = ttk.Frame(main_frame, style="Dark.TFrame")
        content_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        content_frame.columnconfigure(1, weight=1)
        content_frame.rowconfigure(0, weight=1)
        
        self.setup_categories_panel(content_frame)
        self.setup_downloads_panel(content_frame)

        self.ffmpeg_status_var = tk.StringVar()
        self.ffmpeg_status_label = ttk.Label(
            main_frame, textvariable=self.ffmpeg_status_var, style="Muted.TLabel",
            justify=tk.LEFT, wraplength=1000,
        )
        self.ffmpeg_status_label.grid(row=3, column=0, sticky=tk.W, pady=(8, 0))

    def refresh_ffmpeg_status(self):
        """Show whether both FFmpeg tools are available and where they live."""
        tools = FormatSelector.discover_ffmpeg(
            configured_path=config_manager.get("ffmpeg_location", ""),
        )
        self.ffmpeg_tools = tools
        deno_runtime = discover_deno(
            configured_path=config_manager.get("deno_location", ""),
        )
        if tools:
            ffmpeg_status = (
                f"FFmpeg: Available  |  FFprobe: Available  |  Location: {tools.location}"
            )
        else:
            ffmpeg_status = (
                "FFmpeg not configured. Maximum YouTube quality may not be available "
                "because high-resolution video and audio streams often require merging. "
                "Use Configure FFmpeg to select its bin directory."
            )
        runtime_status = (
            f"JavaScript Runtime: Deno {deno_runtime.version} ({deno_runtime.path})"
            if deno_runtime
            else "JavaScript Runtime: Not available. Some YouTube formats may be unavailable."
        )
        self.ffmpeg_status_var.set(f"{ffmpeg_status}\n{runtime_status}")

    def configure_ffmpeg(self):
        """Save a verified FFmpeg directory without requiring a PATH change."""
        current = config_manager.get("ffmpeg_location", "")
        initialdir = current if current and os.path.isdir(current) else None
        directory = filedialog.askdirectory(
            title="Select the folder containing ffmpeg and ffprobe",
            initialdir=initialdir,
            parent=self.root,
        )
        if not directory:
            return
        tools = FormatSelector.discover_ffmpeg(configured_path=directory)
        if not tools:
            messagebox.showerror(
                "FFmpeg tools not found",
                "The selected folder must contain working ffmpeg and ffprobe executables.",
                parent=self.root,
            )
            return
        config_manager.set("ffmpeg_location", tools.location)
        config_manager.save_config()
        self.refresh_ffmpeg_status()

    def show_settings(self):
        window = tk.Toplevel(self.root)
        window.title("Settings and diagnostics")
        window.transient(self.root)
        window.grab_set()
        window.geometry("700x680")
        body = ttk.Frame(window, padding=14)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(1, weight=1)

        download_path = tk.StringVar(value=config_manager.get("default_download_path"))
        concurrency = tk.IntVar(value=config_manager.get("max_concurrent_downloads", 3))
        connections = tk.IntVar(value=config_manager.get("default_connections", 16))
        history_limit = tk.IntVar(value=config_manager.get("history_limit", 200))
        logging_level = tk.StringVar(value=config_manager.get("logging_level", "INFO"))
        video_quality = tk.StringVar(value=config_manager.get("default_video_quality", "Best Available"))
        audio_quality = tk.StringVar(value=config_manager.get("default_audio_quality", "Best Original Audio"))
        download_type = tk.StringVar(value=config_manager.get("default_download_type", "Video"))
        default_download_subtitles = tk.BooleanVar(value=config_manager.get("download_subtitles", False))
        default_embed_subtitles = tk.BooleanVar(value=config_manager.get("embed_subtitles", False))
        default_save_thumbnail = tk.BooleanVar(value=config_manager.get("save_thumbnail", False))
        default_embed_thumbnail = tk.BooleanVar(value=config_manager.get("embed_thumbnail", False))
        default_embed_metadata = tk.BooleanVar(value=config_manager.get("embed_metadata", False))
        default_subtitle_languages = tk.StringVar(value=", ".join(config_manager.get("subtitle_languages", [])))
        youtube_browser_session = tk.StringVar(value=config_manager.get("youtube_browser_session", "Off"))
        ttk.Label(
            body, text="Download folder (existing files are renamed, never overwritten)"
        ).grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(body, textvariable=download_path).grid(row=0, column=1, sticky=tk.EW, padx=8)
        ttk.Button(body, text="Browse", command=lambda: download_path.set(
            filedialog.askdirectory(initialdir=download_path.get(), parent=window) or download_path.get()
        )).grid(row=0, column=2)
        ttk.Label(body, text="Maximum simultaneous downloads").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Spinbox(body, from_=1, to=8, textvariable=concurrency, width=6).grid(row=1, column=1, sticky=tk.W, padx=8)
        ttk.Label(body, text="Video fragments per download").grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Spinbox(body, from_=1, to=32, textvariable=connections, width=6).grid(row=2, column=1, sticky=tk.W, padx=8)
        ttk.Label(body, text="Default video quality").grid(row=3, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(body, textvariable=video_quality, state="readonly", values=[
            "Best Available", "Best MP4 Compatible", "2160p / 4K", "1440p / 2K",
            "1080p", "720p", "480p", "360p",
        ]).grid(row=3, column=1, sticky=tk.W, padx=8)
        ttk.Label(body, text="Default audio quality").grid(row=4, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(body, textvariable=audio_quality, state="readonly", values=[
            "Best Original Audio", "MP3 High Quality",
        ]).grid(row=4, column=1, sticky=tk.W, padx=8)
        ttk.Label(body, text="Default download type").grid(row=5, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(body, textvariable=download_type, state="readonly", values=[
            "Video", "Audio Only",
        ]).grid(row=5, column=1, sticky=tk.W, padx=8)
        media_defaults = ttk.LabelFrame(body, text="Optional media defaults", padding=6)
        media_defaults.grid(row=6, column=0, columnspan=3, sticky=tk.EW, pady=(8, 4))
        ttk.Checkbutton(media_defaults, text="Download subtitles", variable=default_download_subtitles).grid(row=0, column=0, sticky=tk.W)
        ttk.Checkbutton(media_defaults, text="Embed subtitles", variable=default_embed_subtitles).grid(row=0, column=1, sticky=tk.W)
        ttk.Checkbutton(media_defaults, text="Save thumbnail", variable=default_save_thumbnail).grid(row=1, column=0, sticky=tk.W)
        ttk.Checkbutton(media_defaults, text="Embed thumbnail", variable=default_embed_thumbnail).grid(row=1, column=1, sticky=tk.W)
        ttk.Checkbutton(media_defaults, text="Embed metadata", variable=default_embed_metadata).grid(row=2, column=0, sticky=tk.W)
        ttk.Label(media_defaults, text="Default languages (comma-separated)").grid(row=2, column=1, sticky=tk.W)
        ttk.Entry(media_defaults, textvariable=default_subtitle_languages, width=24).grid(row=3, column=1, sticky=tk.W)
        ttk.Label(body, text="History entries to retain").grid(row=7, column=0, sticky=tk.W, pady=5)
        ttk.Spinbox(body, from_=10, to=5000, textvariable=history_limit, width=8).grid(row=7, column=1, sticky=tk.W, padx=8)
        ttk.Label(body, text="Logging level").grid(row=8, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(body, textvariable=logging_level, state="readonly", values=[
            "INFO", "WARNING", "ERROR", "DEBUG",
        ], width=12).grid(row=8, column=1, sticky=tk.W, padx=8)

        tools = FormatSelector.discover_ffmpeg(config_manager.get("ffmpeg_location", ""))
        runtime = discover_deno(config_manager.get("deno_location", ""))
        ffmpeg_text = f"FFmpeg: Available\nFFprobe: Available\nLocation: {tools.location}" if tools else "FFmpeg not configured. High-resolution split streams may need merging."
        deno_text = f"Deno: {runtime.version}\nLocation: {runtime.path}" if runtime else "Deno: unavailable (some YouTube formats may not be listed)."
        ttk.Label(
            body, text=ffmpeg_text, justify=tk.LEFT, wraplength=650,
        ).grid(row=9, column=0, columnspan=3, sticky=tk.W, pady=(12, 4))
        ttk.Label(
            body, text=deno_text, justify=tk.LEFT, wraplength=650,
        ).grid(row=10, column=0, columnspan=3, sticky=tk.W, pady=4)

        def configure_deno():
            path = filedialog.askopenfilename(
                title="Select deno.exe", filetypes=[("Deno executable", "deno.exe"), ("All files", "*.*")], parent=window
            )
            if not path:
                return
            detected = discover_deno(path)
            if not detected:
                messagebox.showerror("Deno not available", "The selected executable failed its version check.", parent=window)
                return
            config_manager.set("deno_location", detected.path)
            config_manager.save_config()
            messagebox.showinfo("Deno configured", f"Deno {detected.version} is available.", parent=window)

        ttk.Button(body, text="Configure FFmpeg", command=self.configure_ffmpeg).grid(row=11, column=0, sticky=tk.W, pady=6)
        ttk.Button(body, text="Configure Deno", command=configure_deno).grid(row=11, column=1, sticky=tk.W, pady=6, padx=8)

        def copy_diagnostics():
            try:
                import yt_dlp
                ytdlp_version = yt_dlp.version.__version__
            except Exception:
                ytdlp_version = "unavailable"
            current_tools = FormatSelector.discover_ffmpeg(config_manager.get("ffmpeg_location", ""))
            current_deno = discover_deno(config_manager.get("deno_location", ""))

            def tool_version(executable):
                if not executable:
                    return "unavailable"
                try:
                    result = subprocess.run(
                        [executable, "-version"], check=True, capture_output=True,
                        text=True, timeout=5,
                    )
                    return (result.stdout or result.stderr).splitlines()[0]
                except (OSError, subprocess.SubprocessError, IndexError):
                    return "unavailable"

            lines = [
                f"Python: {os.sys.version.split()[0]}",
                f"yt-dlp: {ytdlp_version}",
                f"FFmpeg: {current_tools.ffmpeg if current_tools else 'unavailable'}",
                f"FFmpeg version: {tool_version(current_tools.ffmpeg) if current_tools else 'unavailable'}",
                f"FFprobe: {current_tools.ffprobe if current_tools else 'unavailable'}",
                f"FFprobe version: {tool_version(current_tools.ffprobe) if current_tools else 'unavailable'}",
                f"Deno: {current_deno.path + ' (' + current_deno.version + ')' if current_deno else 'unavailable'}",
                f"Download folder: {download_path.get()}",
                f"Log file: {configure_application_logging(config_manager.get('logging_level', 'INFO'))}",
            ]
            window.clipboard_clear()
            window.clipboard_append("\n".join(lines))
            messagebox.showinfo("Diagnostics copied", "Version and tool paths copied without credentials.", parent=window)

        ttk.Button(body, text="Copy diagnostics", command=copy_diagnostics).grid(row=12, column=0, sticky=tk.W, pady=(10, 4))
        ttk.Label(body, text="YouTube browser session (optional)").grid(row=12, column=1, sticky=tk.E, padx=8)
        ttk.Combobox(body, textvariable=youtube_browser_session, state="readonly",
                     values=["Off", "Brave", "Edge", "Chrome", "Firefox"], width=10).grid(
                         row=12, column=2, sticky=tk.W)

        def save_settings():
            try:
                limit = int(concurrency.get())
                fragment_count = int(connections.get())
            except (ValueError, tk.TclError):
                messagebox.showerror("Invalid settings", "Concurrency values must be whole numbers.", parent=window)
                return
            if not download_path.get().strip() or not os.path.isdir(download_path.get()):
                messagebox.showerror("Invalid folder", "Choose an existing download folder.", parent=window)
                return
            if not 1 <= limit <= 8 or not 1 <= fragment_count <= 32:
                messagebox.showerror("Invalid settings", "Concurrency must be between 1 and 8; fragments between 1 and 32.", parent=window)
                return
            config_manager.set("default_download_path", os.path.abspath(download_path.get()))
            config_manager.set("max_concurrent_downloads", limit)
            config_manager.set("default_connections", fragment_count)
            config_manager.set("default_video_quality", video_quality.get())
            config_manager.set("default_audio_quality", audio_quality.get())
            config_manager.set("default_download_type", download_type.get())
            try:
                config_manager.set("history_limit", int(history_limit.get()))
            except (ValueError, tk.TclError):
                messagebox.showerror("Invalid settings", "History limit must be a whole number.", parent=window)
                return
            config_manager.set("logging_level", logging_level.get())
            config_manager.set("download_subtitles", default_download_subtitles.get())
            config_manager.set("embed_subtitles", default_embed_subtitles.get())
            config_manager.set("save_thumbnail", default_save_thumbnail.get())
            config_manager.set("embed_thumbnail", default_embed_thumbnail.get())
            config_manager.set("embed_metadata", default_embed_metadata.get())
            config_manager.set("subtitle_languages", [
                language.strip() for language in default_subtitle_languages.get().split(",")
                if language.strip()
            ])
            config_manager.set("youtube_browser_session", youtube_browser_session.get())
            config_manager.save_config()
            configure_application_logging(logging_level.get())
            self.download_manager.max_concurrent = limit
            self.location_var.set(config_manager.get("default_download_path"))
            self.connections_var.set(fragment_count)
            self.video_quality_var.set(video_quality.get())
            self.audio_quality_var.set(audio_quality.get())
            self.download_type_var.set(download_type.get())
            self.on_download_type_change()
            self.download_manager._process_queue()
            window.destroy()

        buttons = ttk.Frame(body)
        buttons.grid(row=13, column=0, columnspan=3, sticky=tk.E, pady=(12, 0))
        ttk.Button(buttons, text="Save settings", command=save_settings).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side=tk.RIGHT, padx=8)

    def close_application(self):
        """Persist resumable work before closing the Tk window."""
        self.download_manager._persist_downloads()
        self.root.destroy()

    def setup_categories_panel(self, parent):
        """Left categories tree"""
        panel = ttk.Frame(parent, style="Card.TFrame", padding="10")
        panel.grid(row=0, column=0, sticky=(tk.N, tk.S), padx=(0, 10))
        panel.rowconfigure(1, weight=1)
        ttk.Label(panel, text="Categories", style="Card.TLabel").grid(row=0, column=0, sticky=tk.W, pady=(0, 6))
        self.categories_tree = ttk.Treeview(panel, show='tree', height=18, selectmode='browse')
        self.categories_tree.grid(row=1, column=0, sticky=(tk.N, tk.S, tk.E, tk.W))
        categories = {
            "All Downloads": ["Compressed", "Documents", "Music", "Programs", "Video"],
            "Queues": [],
            "Finished": [],
            "Unfinished": []
        }
        for parent_label, children in categories.items():
            parent_id = self.categories_tree.insert('', 'end', text=parent_label, open=True)
            for child in children:
                self.categories_tree.insert(parent_id, 'end', text=child, open=True)
        self.categories_tree.selection_set(self.categories_tree.get_children('')[0])

    def setup_downloads_panel(self, parent):
        """Right panel with options and list"""
        right_panel = ttk.Frame(parent, style="Dark.TFrame")
        right_panel.grid(row=0, column=1, sticky=(tk.N, tk.S, tk.E, tk.W))
        right_panel.rowconfigure(1, weight=1)
        right_panel.columnconfigure(0, weight=1)
        
        options_frame = ttk.Frame(right_panel, style="Dark.TFrame")
        options_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        options_frame.columnconfigure(1, weight=1)
        options_frame.columnconfigure(3, weight=1)
        options_frame.columnconfigure(5, weight=1)
        
        ttk.Label(options_frame, text="Type:", style="Dark.TLabel").grid(row=0, column=0, sticky=tk.W)
        self.download_type_var = tk.StringVar(value=config_manager.get("default_download_type", "Video"))
        download_type_combo = ttk.Combobox(options_frame, textvariable=self.download_type_var, 
                                           values=["Video", "Audio Only"], state="readonly", width=12)
        download_type_combo.grid(row=0, column=1, padx=5, sticky=tk.W)
        download_type_combo.bind('<<ComboboxSelected>>', self.on_download_type_change)
        
        ttk.Label(options_frame, text="Video Quality:", style="Dark.TLabel").grid(row=0, column=2, padx=(10, 5), sticky=tk.W)
        self.video_quality_var = tk.StringVar(value=config_manager.get("default_video_quality", "Best Available"))
        self.video_quality_combo = ttk.Combobox(options_frame, textvariable=self.video_quality_var,
                                                values=["Best Available", "Best MP4 Compatible", "2160p / 4K", "1440p / 2K", "1080p", "720p", "480p", "360p"],
                                                state="readonly", width=15)
        self.video_quality_combo.grid(row=0, column=3, padx=5, sticky=tk.W)
        
        ttk.Label(options_frame, text="Audio Quality:", style="Dark.TLabel").grid(row=0, column=4, padx=(10, 5), sticky=tk.W)
        self.audio_quality_var = tk.StringVar(value=config_manager.get("default_audio_quality", "Best Original Audio"))
        self.audio_quality_combo = ttk.Combobox(options_frame, textvariable=self.audio_quality_var,
                                                 values=["Best Original Audio", "MP3 High Quality"],
                                                 state="readonly", width=22)
        self.audio_quality_combo.grid(row=0, column=5, padx=5, sticky=(tk.W, tk.E))
        
        ttk.Label(options_frame, text="Video fragments:", style="Dark.TLabel").grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        self.connections_var = tk.IntVar(value=config_manager.get("default_connections", 16))
        connections_spin = ttk.Spinbox(options_frame, from_=1, to=32, textvariable=self.connections_var, width=5, justify='center')
        connections_spin.grid(row=1, column=1, sticky=tk.W, padx=5, pady=(5, 0))
        
        # Downloads list frame
        list_frame = ttk.Frame(right_panel, style="Card.TFrame")
        list_frame.grid(row=1, column=0, sticky=(tk.N, tk.S, tk.E, tk.W), pady=(12, 0))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        
        # Treeview for downloads
        columns = ('Filename', 'Quality', 'Status', 'Progress', 'Speed', 'Size', 'ETA')
        self.tree = ttk.Treeview(list_frame, columns=columns, show='tree headings', height=15,
                                 selectmode='browse', style="Dark.Treeview")
        self.tree.heading('#0', text='ID')
        self.tree.column('#0', width=100)
        
        # Enable single click selection
        self.tree.bind('<Button-1>', self.on_tree_click)
        self.tree.bind('<Double-1>', self.show_selected_error)
        self.tree.bind('<Button-3>', self.show_download_context_menu)
        self.download_context_menu = tk.Menu(self.root, tearoff=False)
        self.download_context_menu.add_command(label="Retry / Resume", command=self.retry_selected)
        self.download_context_menu.add_command(label="Move to front of queue", command=self.move_selected_to_front)
        self.download_context_menu.add_command(label="Move to end of queue", command=self.move_selected_to_back)
        self.download_context_menu.add_command(label="Open downloaded file", command=self.open_selected_file)
        self.download_context_menu.add_command(label="Open download folder", command=self.open_selected_folder)
        self.download_context_menu.add_separator()
        self.download_context_menu.add_command(label="Remove entry (keep media)", command=self.remove_selected)
        
        for col in columns:
            self.tree.heading(col, text=col)
            if col == 'Progress':
                self.tree.column(col, width=105)
            elif col == 'Filename':
                self.tree.column(col, width=150)
            elif col == 'Quality':
                self.tree.column(col, width=160)
            elif col == 'Size':
                self.tree.column(col, width=115)
            elif col == 'ETA':
                self.tree.column(col, width=75)
            else:
                self.tree.column(col, width=100)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        horizontal_scrollbar = ttk.Scrollbar(
            list_frame, orient=tk.HORIZONTAL, command=self.tree.xview
        )
        self.tree.configure(
            yscrollcommand=scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set,
        )
        
        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        horizontal_scrollbar.grid(row=1, column=0, sticky=(tk.W, tk.E))
        ttk.Label(
            list_frame,
            text="Right-click a download for retry, queue order, open file/folder, or remove entry (media is kept).",
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky=tk.W, pady=(5, 0))
        
        self.on_download_type_change()
        
    def on_tree_click(self, event):
        """Handle tree click to ensure selection works"""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)

    def show_download_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.download_context_menu.tk_popup(event.x_root, event.y_root)

    def show_selected_error(self, _event=None):
        download_id = self.get_selected_download_id()
        info = self.download_manager.get_download_info(download_id) if download_id else None
        if info and info.get("error"):
            error_info = UserFriendlyError.get_user_friendly_message(
                RuntimeError(info["error"]), info.get("error_category") or "UNKNOWN_ERROR"
            )
            messagebox.showerror("Download error", UserFriendlyError.format_error_display(error_info))
    
    def on_download_type_change(self, event=None):
        """Handle download type change"""
        download_type = self.download_type_var.get()
        if download_type == "Audio Only":
            self.video_quality_combo.config(state="disabled")
            self.audio_quality_combo.config(state="readonly")
        else:
            self.video_quality_combo.config(state="readonly")
            self.audio_quality_combo.config(state="disabled")
    
    def browse_location(self):
        """Browse for download location"""
        directory = filedialog.askdirectory(initialdir=self.location_var.get())
        if directory:
            self.location_var.set(directory)
    
    def add_download(self):
        """Add a new download with user-friendly error handling"""
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("Warning", "Please enter a valid URL")
            return

        if VideoAnalyzer.is_video_url(url):
            if self.video_inspection_pending:
                messagebox.showinfo("Video information", "A video inspection is already in progress.")
                return
            self.video_inspection_pending = True
            threading.Thread(
                target=self._inspect_video,
                args=(url,),
                daemon=True,
            ).start()
            return

        self._queue_download(url)

    def _available_video_quality_choices(self, info):
        fixed_choices = ["Best Available", "Best MP4 Compatible"]
        heights = info.get("resolutions") or []
        if not heights:
            return fixed_choices + [
                "2160p / 4K", "1440p / 2K", "1080p", "720p", "480p", "360p"
            ]
        choices = fixed_choices
        for height in heights:
            if height == 2160:
                choices.append("2160p / 4K")
            elif height == 1440:
                choices.append("1440p / 2K")
            else:
                choices.append(f"{height}p")
        return choices

    def _apply_video_quality_choices(self, info):
        if info.get("is_playlist"):
            return
        choices = self._available_video_quality_choices(info)
        self.video_quality_combo.configure(values=choices)
        if self.video_quality_var.get() not in choices:
            self.video_quality_var.set("Best Available")

    def _inspect_video(self, url):
        try:
            result = VideoAnalyzer.get_video_info(url)
            self.video_info_queue.put((url, result, None))
        except Exception as exc:
            logging.getLogger(__name__).exception("Video metadata inspection failed")
            self.video_info_queue.put((url, None, exc))

    def _queue_download(
        self,
        url,
        custom_name=None,
        ignore_global_filename=False,
        optional_media_options=None,
        playlist_group_id=None,
        playlist_title=None,
        playlist_index=None,
        playlist_count=None,
    ):
        location = self.location_var.get()
        if not os.path.exists(location):
            try:
                os.makedirs(location, exist_ok=True)
            except Exception as e:
                error_info = UserFriendlyError.get_user_friendly_message(e)
                messagebox.showerror("Error", UserFriendlyError.format_error_display(error_info))
                return
        
        try:
            # Get download options
            download_type = self.download_type_var.get()
            video_quality = self.video_quality_var.get()
            audio_quality = self.audio_quality_var.get()
            if custom_name is None and not ignore_global_filename:
                custom_name = self.filename_var.get().strip()
                custom_name = custom_name if custom_name else None
            connections = max(1, min(32, self.connections_var.get()))
            
            download_id = self.download_manager.add_download(
                url, 
                location,
                num_threads=connections,
                download_type=download_type,
                video_quality=video_quality,
                audio_quality=audio_quality,
                custom_filename=custom_name,
                playlist_group_id=playlist_group_id,
                playlist_title=playlist_title,
                playlist_index=playlist_index,
                playlist_count=playlist_count,
                **(optional_media_options or {}),
            )
            self.url_entry.delete(0, tk.END)
            self.refresh_downloads_list()
        except Exception as e:
            logging.getLogger(__name__).exception("Could not queue download")
            category = DownloadManager._classify_ytdlp_error(e)
            error_info = UserFriendlyError.get_user_friendly_message(e, category)
            messagebox.showerror("Download Error", UserFriendlyError.format_error_display(error_info))

    def _show_video_inspection(self, url, info):
        if not info:
            messagebox.showerror("Video information", "yt-dlp returned no information for this URL.")
            return

        window = tk.Toplevel(self.root)
        window.title("Video information")
        window.transient(self.root)
        window.grab_set()
        window.geometry("780x760")
        window.minsize(720, 640)

        title = info.get("playlist_title") if info.get("is_playlist") else info.get("title")
        title = title or "Video"
        detail_lines = [
            f"Title: {title}",
            f"Channel: {info.get('channel') or info.get('uploader') or 'Unknown'}",
            f"Duration: {info.get('duration_string') or info.get('duration') or 'Unknown'}",
            f"Available resolutions: {', '.join(map(str, info.get('resolutions') or [])) or 'Unknown'}",
            f"FPS: {info.get('fps') or 'Unknown'}",
            f"Video codec: {info.get('video_codec') or 'Unknown'}",
            f"Audio codec: {info.get('audio_codec') or 'Unknown'}",
            f"Container: {info.get('format') or 'Selected automatically'}",
            f"Estimated size: {self.format_size(info.get('filesize') or 0) if info.get('filesize') else 'Unknown'}",
            f"Subtitles: {', '.join(info.get('subtitles') or []) or 'None listed'}",
            f"Automatic subtitles: {', '.join(info.get('automatic_subtitles') or []) or 'None listed'}",
        ]
        if info.get("thumbnail"):
            detail_lines.append(f"Thumbnail: {info['thumbnail']}")
        ffmpeg_tools = FormatSelector.discover_ffmpeg(
            configured_path=config_manager.get("ffmpeg_location", ""),
        )
        if ffmpeg_tools:
            detail_lines.append(f"FFmpeg/ffprobe: {ffmpeg_tools.ffmpeg} / {ffmpeg_tools.ffprobe}")
        else:
            detail_lines.append("FFmpeg/ffprobe: not found (split high-quality streams cannot be merged)")
        self._apply_video_quality_choices(info)
        details = ttk.Label(window, text="\n".join(detail_lines), justify=tk.LEFT, wraplength=560)
        details.pack(fill=tk.X, padx=12, pady=12)

        entries = info.get("entries") or []
        listbox = None
        media_options_frame = ttk.LabelFrame(window, text="Optional media files", padding=8)
        media_options_frame.pack(fill=tk.X, padx=12, pady=6)
        subtitles = sorted(set(info.get("subtitles") or []) | set(info.get("automatic_subtitles") or []))
        subtitle_download_var = tk.BooleanVar(value=config_manager.get("download_subtitles", False))
        subtitle_embed_var = tk.BooleanVar(value=config_manager.get("embed_subtitles", False))
        thumbnail_save_var = tk.BooleanVar(value=config_manager.get("save_thumbnail", False))
        thumbnail_embed_var = tk.BooleanVar(value=config_manager.get("embed_thumbnail", False))
        metadata_embed_var = tk.BooleanVar(value=config_manager.get("embed_metadata", False))
        ttk.Checkbutton(
            media_options_frame, text="Download subtitles", variable=subtitle_download_var
        ).grid(row=0, column=0, sticky=tk.W)
        ttk.Checkbutton(
            media_options_frame, text="Embed subtitles (FFmpeg required)", variable=subtitle_embed_var
        ).grid(row=1, column=0, sticky=tk.W)
        ttk.Checkbutton(
            media_options_frame, text="Save thumbnail", variable=thumbnail_save_var
        ).grid(row=2, column=0, sticky=tk.W)
        ttk.Checkbutton(
            media_options_frame, text="Embed thumbnail (FFmpeg required)", variable=thumbnail_embed_var
        ).grid(row=3, column=0, sticky=tk.W)
        ttk.Checkbutton(
            media_options_frame, text="Embed media metadata", variable=metadata_embed_var
        ).grid(row=4, column=0, sticky=tk.W)
        ttk.Label(media_options_frame, text="Subtitle languages (available from metadata):").grid(
            row=0, column=1, sticky=tk.W, padx=(14, 4)
        )
        language_list = tk.Listbox(media_options_frame, selectmode=tk.EXTENDED, height=4, exportselection=False)
        language_list.grid(row=1, column=1, rowspan=4, sticky=(tk.N, tk.S, tk.E, tk.W), padx=(14, 4))
        for language in subtitles:
            language_list.insert(tk.END, language)
        preferred_languages = config_manager.get("subtitle_languages", [])
        for index, language in enumerate(subtitles):
            if language in preferred_languages:
                language_list.selection_set(index)
        if not subtitles:
            language_list.insert(tk.END, "No subtitles listed")
            language_list.configure(state=tk.DISABLED)
        media_options_frame.columnconfigure(1, weight=1)
        media_options = {}
        if info.get("is_playlist"):
            ttk.Label(
                window,
                text=f"Playlist videos ({info.get('playlist_count') or len(entries)} available). Select the videos to add:",
            ).pack(anchor=tk.W, padx=12)
            list_frame = ttk.Frame(window)
            list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
            listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED, height=10)
            scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=listbox.yview)
            listbox.configure(yscrollcommand=scrollbar.set)
            listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            for entry in entries:
                listbox.insert(tk.END, entry.get("title") or entry.get("id") or "Untitled video")
        if info.get("thumbnail"):
            preview_frame = ttk.LabelFrame(window, text="Thumbnail preview", padding=8)
            preview_frame.pack(fill=tk.X, padx=12, pady=6)
            preview_label = ttk.Label(preview_frame, text="No preview loaded", width=42)
            preview_label.pack(side=tk.LEFT, padx=(0, 10))
            preview_status = tk.StringVar(value="Preview loads only when requested.")
            preview_controls = ttk.Frame(preview_frame)
            preview_controls.pack(side=tk.LEFT, fill=tk.X, expand=True)
            preview_button = ttk.Button(preview_controls, text="Preview thumbnail")
            preview_button.configure(command=lambda: self._start_thumbnail_preview(
                info["thumbnail"], window, preview_label, preview_status, preview_button,
            ))
            preview_button.pack(anchor=tk.W)
            ttk.Label(
                preview_controls, textvariable=preview_status, wraplength=300,
            ).pack(anchor=tk.W, pady=(6, 0))

        buttons = ttk.Frame(window)
        buttons.pack(fill=tk.X, padx=12, pady=12)

        def add_selected():
            ffmpeg_tools = FormatSelector.discover_ffmpeg(
                configured_path=config_manager.get("ffmpeg_location", ""),
            )
            if (
                self.download_type_var.get() == "Audio Only"
                and FormatSelector.requires_ffmpeg(
                    "Audio Only", self.audio_quality_var.get()
                )
                and not ffmpeg_tools
            ):
                messagebox.showerror(
                    "FFmpeg required",
                    "MP3 conversion requires FFmpeg and ffprobe. Configure their "
                    "location in app_config.json or add them to PATH.",
                    parent=window,
                )
                return
            if (
                self.download_type_var.get() != "Audio Only"
                and self.video_quality_var.get() != "Best MP4 Compatible"
                and not ffmpeg_tools
                and not messagebox.askyesno(
                    "Maximum quality unavailable",
                    "FFmpeg is required to download and merge the highest available "
                    "video and audio quality. Falling back to a combined format "
                    "may reduce quality. Continue?",
                    parent=window,
                )
            ):
                return
            if listbox is None:
                requested_name = self.filename_var.get().strip()
                chosen = [(url, requested_name or info.get("title") or "video")]
                playlist_group_id = None
            else:
                indexes = listbox.curselection()
                if not indexes:
                    messagebox.showwarning("Playlist selection", "Select one or more videos first.", parent=window)
                    return
                chosen = []
                for index in indexes:
                    entry = entries[index]
                    item_url = entry.get("url")
                    item_id = entry.get("id")
                    if not item_url or not str(item_url).startswith(("http://", "https://")):
                        extractor = str(info.get("extractor") or "").lower()
                        if item_id and extractor.startswith("youtube"):
                            item_url = f"https://www.youtube.com/watch?v={item_id}"
                    if item_url:
                        chosen.append((item_url, entry.get("title") or item_id or "video"))
                if not chosen:
                    messagebox.showerror("Playlist selection", "Selected entries have no usable video URLs.", parent=window)
                    return
                playlist_group_id = uuid.uuid4().hex
            selected_languages = [
                subtitles[index] for index in language_list.curselection()
                if index < len(subtitles)
            ]
            if self.download_type_var.get() != "Audio Only":
                media_options.update({
                    "subtitle_languages": selected_languages,
                    "download_subtitles": subtitle_download_var.get(),
                    "embed_subtitles": subtitle_embed_var.get(),
                    "save_thumbnail": thumbnail_save_var.get(),
                    "embed_thumbnail": thumbnail_embed_var.get(),
                    "embed_metadata": metadata_embed_var.get(),
                })
                if (subtitle_embed_var.get() or thumbnail_embed_var.get()) and not ffmpeg_tools:
                    messagebox.showwarning(
                        "FFmpeg unavailable",
                        "The selected media will download, but embedding subtitles or a thumbnail needs FFmpeg. The corresponding embedding option will be skipped.",
                        parent=window,
                    )
            window.destroy()
            for index, (item_url, custom_name) in enumerate(chosen, start=1):
                self._queue_download(
                    item_url,
                    custom_name,
                    ignore_global_filename=listbox is not None,
                    optional_media_options=media_options,
                    playlist_group_id=playlist_group_id,
                    playlist_title=info.get("playlist_title") if listbox is not None else None,
                    playlist_index=index if listbox is not None else None,
                    playlist_count=len(chosen) if listbox is not None else None,
                )
            self.url_entry.delete(0, tk.END)

        if listbox is not None:
            ttk.Button(buttons, text="Select all", command=lambda: listbox.select_set(0, tk.END)).pack(side=tk.LEFT)
            ttk.Button(buttons, text="Clear all", command=lambda: listbox.selection_clear(0, tk.END)).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Add selected", command=add_selected).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side=tk.RIGHT, padx=6)

    def _start_thumbnail_preview(self, url, window, label, status, button):
        window_key = str(window)
        if window_key in self.thumbnail_previews_in_flight:
            return
        self.thumbnail_previews_in_flight.add(window_key)
        button.configure(state=tk.DISABLED)
        status.set("Loading preview…")
        threading.Thread(
            target=self._load_thumbnail_preview,
            args=(url, window, label, status, button, window_key),
            daemon=True,
        ).start()

    def _load_thumbnail_preview(self, url, window, label, status, button, window_key):
        try:
            image = fetch_thumbnail_image(url)
            result = (window, label, status, button, window_key, image, None)
        except Exception:
            logging.getLogger(__name__).warning(
                "Could not load optional thumbnail preview", exc_info=True
            )
            result = (window, label, status, button, window_key, None, "Preview unavailable")
        self.thumbnail_preview_queue.put(result)
    
    def format_size(self, size_bytes):
        """Format bytes to human readable format"""
        if size_bytes == 0:
            return "0 B"
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"
    
    def format_speed(self, speed_bytes):
        """Format speed to show megabytes per second only"""
        if speed_bytes <= 0:
            return "0 MB/s"
        speed_mb = speed_bytes / (1024 * 1024)
        return f"{speed_mb:.2f} MB/s"
    
    def format_time(self, seconds):
        """Format seconds to human readable time"""
        if seconds <= 0:
            return "--:--"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"
    
    def refresh_downloads_list(self):
        """Refresh the downloads list in the treeview"""
        # Updating existing rows preserves selection and avoids rebuilding the
        # entire widget four times a second while downloads are active.
        existing = {
            self.tree.item(item, 'text'): item
            for item in self.tree.get_children()
        }
        downloads = self.download_manager.get_all_downloads()
        for download in downloads:
            if download:
                download_id = download['id']
                item = existing.pop(download_id, None)
                filename = download['filename']
                download_status = download['status']
                status = download_status
                if download.get("playlist_count"):
                    overall = download.get("playlist_progress") or 0
                    status = (
                        f"{status} · {download.get('playlist_index')}/"
                        f"{download['playlist_count']} · playlist ~{overall:.0f}%"
                    )
                progress = f"{download['progress']:.1f}%"
                speed_value = download.get('speed', 0) or 0
                speed = self.format_speed(speed_value)
                size = f"{self.format_size(download['downloaded'])} / {self.format_size(download['file_size'])}"
                eta = self.format_time(download['eta'])
                
                shown_status = {
                    "Completed": "✓ Completed", "Error": "✗ Error",
                    "Paused": "⏸ Paused", "Downloading": "⬇ Downloading",
                    "Queued": "⏳ Queued", "Analyzing": "🔍 Analyzing",
                    "Cancelled": "✕ Cancelled",
                }.get(download_status, status)
                values = (
                    filename, download.get("quality") or "—", shown_status,
                    progress, speed, size, eta,
                )
                if item is None:
                    self.tree.insert('', 'end', text=download_id, values=values,
                                     tags=(download_status.lower(),))
                elif self.tree.item(item, 'values') != tuple(str(value) for value in values):
                    self.tree.item(item, values=values, tags=(download_status.lower(),))
        for item in existing.values():
            self.tree.delete(item)
    
    def update_ui(self):
        """Periodically update the UI"""
        while True:
            try:
                window, label, status, button, window_key, image, error = self.thumbnail_preview_queue.get_nowait()
            except queue.Empty:
                break
            self.thumbnail_previews_in_flight.discard(window_key)
            try:
                if not window.winfo_exists():
                    continue
                if error:
                    status.set(error)
                    button.configure(state=tk.NORMAL)
                    continue
                photo = ImageTk.PhotoImage(image, master=window)
                label.configure(image=photo, text="", width=320)
                label.image = photo
                status.set("Thumbnail preview ready")
                button.configure(state=tk.NORMAL)
            except tk.TclError:
                continue
        while True:
            try:
                url, info, error = self.video_info_queue.get_nowait()
            except queue.Empty:
                break
            self.video_inspection_pending = False
            if error is not None:
                category = DownloadManager._classify_ytdlp_error(error)
                error_info = UserFriendlyError.get_user_friendly_message(error, category)
                messagebox.showerror(
                    "Video information error",
                    UserFriendlyError.format_error_display(error_info),
                )
            else:
                self._show_video_inspection(url, info)
        try:
            while True:
                self.ui_refresh_queue.get_nowait()
        except queue.Empty:
            pass
        self.refresh_downloads_list()
        self.root.after(self.update_interval, self.update_ui)

    def on_progress_update(self, _download_id, _downloaded, _file_size):
        """Queue a UI refresh without touching Tk from a worker thread."""
        self._queue_ui_refresh()

    def on_status_update(self, _download_id, _status):
        """Queue a UI refresh without touching Tk from a worker thread."""
        self._queue_ui_refresh()

    def _queue_ui_refresh(self):
        try:
            self.ui_refresh_queue.put_nowait(None)
        except queue.Full:
            pass
    
    def get_selected_download_id(self):
        """Get the selected download ID"""
        selection = self.tree.selection()
        if selection:
            return self.tree.item(selection[0])['text']
        return None
    
    def pause_selected(self):
        """Pause the selected download"""
        download_id = self.get_selected_download_id()
        if download_id:
            self.download_manager.pause_download(download_id)
        else:
            messagebox.showwarning("Warning", "Please select a download to pause.")
    
    def resume_selected(self):
        """Resume the selected download"""
        download_id = self.get_selected_download_id()
        if download_id:
            self.download_manager.resume_download(download_id)
        else:
            messagebox.showwarning("Warning", "Please select a download to resume.")

    def retry_selected(self):
        download_id = self.get_selected_download_id()
        info = self.download_manager.get_download_info(download_id) if download_id else None
        if info and info.get("status") in {"Paused", "Error", "Interrupted"}:
            self.download_manager.resume_download(download_id)
        else:
            messagebox.showinfo("Retry or resume", "Select a paused, interrupted, or failed download.")

    def move_selected_to_front(self):
        download_id = self.get_selected_download_id()
        if not download_id or not self.download_manager.set_download_priority(
            download_id, DownloadPriority.HIGH
        ):
            messagebox.showinfo("Queue order", "Only queued downloads can be moved.")
            return
        self.download_manager._process_queue()

    def move_selected_to_back(self):
        download_id = self.get_selected_download_id()
        if not download_id or not self.download_manager.set_download_priority(
            download_id, DownloadPriority.LOW
        ):
            messagebox.showinfo("Queue order", "Only queued downloads can be moved.")
            return
        self.download_manager._process_queue()

    def open_selected_file(self):
        download_id = self.get_selected_download_id()
        info = self.download_manager.get_download_info(download_id) if download_id else None
        path = info.get("final_file") if info else None
        if not path or not os.path.isfile(path):
            messagebox.showinfo("Open downloaded file", "The selected item has no completed file.")
            return
        startfile = getattr(os, "startfile", None)
        if startfile:
            startfile(path)
        else:
            messagebox.showinfo("Open downloaded file", "Opening files is supported on Windows.")

    def open_selected_folder(self):
        download_id = self.get_selected_download_id()
        info = self.download_manager.get_download_info(download_id) if download_id else None
        folder = info.get("destination") if info else None
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("Open download folder", "The selected download folder is unavailable.")
            return
        startfile = getattr(os, "startfile", None)
        if startfile:
            startfile(folder)
        else:
            messagebox.showinfo("Open download folder", "Opening folders is supported on Windows.")
    
    def cancel_selected(self):
        """Cancel the selected download with confirmation"""
        download_id = self.get_selected_download_id()
        if download_id:
            download = self.download_manager.downloads.get(download_id)
            if download and download.status in [
                "Downloading", "Pausing", "Cancelling", "Queued", "Paused"
            ]:
                filename = download.current_filename or download.get_filename()
                if messagebox.askyesno("Confirm Cancellation", 
                                     f"Are you sure you want to cancel '{filename}'?"):
                    self.download_manager.cancel_download(download_id)
            else:
                messagebox.showwarning("Warning", "Please select an active download to cancel.")
        else:
            messagebox.showwarning("Warning", "Please select a download to cancel.")
    
    def remove_selected(self):
        """Remove the selected download from the list"""
        download_id = self.get_selected_download_id()
        if download_id:
            download = self.download_manager.downloads.get(download_id)
            if download and download.status in ["Completed", "Error", "Cancelled"]:
                filename = download.current_filename or download.get_filename()
                status = download.status.lower()
                if messagebox.askyesno("Confirm Removal", 
                                     f"Remove the '{filename}' entry ({status}) from the list? The downloaded media file will be kept."):
                    if not self.download_manager.remove_download(download_id):
                        messagebox.showwarning(
                            "Download Busy",
                            "Wait for the download worker to finish before removing it.",
                        )
            elif download:
                messagebox.showwarning("Warning", 
                                     "Cannot remove active download. Cancel it first.")
            else:
                self.download_manager.remove_history_entry(download_id)
        else:
            messagebox.showwarning("Warning", "Please select a download to remove.")
    
    def clear_completed(self):
        """Clear all completed downloads with confirmation"""
        completed_count = 0
        error_count = 0
        cancelled_count = 0
        
        for download in self.download_manager.downloads.values():
            if download.status == "Completed":
                completed_count += 1
            elif download.status == "Error":
                error_count += 1
            elif download.status == "Cancelled":
                cancelled_count += 1
        
        total_count = completed_count + error_count + cancelled_count
        
        if total_count == 0:
            messagebox.showinfo("Info", "No completed downloads to clear.")
            return
        
        message = f"Clear {total_count} completed items from history?\n\n"
        if completed_count > 0:
            message += f"• Completed: {completed_count}\n"
        if error_count > 0:
            message += f"• Errors: {error_count}\n"
        if cancelled_count > 0:
            message += f"• Cancelled: {cancelled_count}"
        
        if messagebox.askyesno("Confirm Clear", message):
            to_remove = []
            for download_id, download in self.download_manager.downloads.items():
                if download.status in ["Completed", "Error", "Cancelled"]:
                    to_remove.append(download_id)
            
            for download_id in to_remove:
                self.download_manager.remove_download(download_id)
            self.download_manager.clear_history_entries()
            
            messagebox.showinfo("Success", f"Cleared {len(to_remove)} items from history.")


def main():
    configure_application_logging(config_manager.get("logging_level", "INFO"))
    root = tk.Tk()
    app = IDMCloneGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

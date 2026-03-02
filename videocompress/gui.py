"""customtkinter GUI for videocompress — GPU-accelerated video compressor.

Provides a modern, dark-themed interface with:
    - Tabbed input pickers (single file or folder)
    - Codec, container, audio, and fallback dropdowns
    - Tabbed Auto / Manual / Camera Profile encoding modes
  - Real-time progress bar and scrolling console output
  - Start / Stop controls with background-threaded encoding

The same ``JobOptions`` / ``run_job`` pipeline used by the CLI is invoked
in a daemon thread so the UI stays responsive during long encodes.

Launch with::

    videocompress gui        # via CLI sub-command
    videocompress-gui        # via direct entry-point
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from videocompress import __version__
from videocompress.inputs import collect_video_files, video_filetypes
from videocompress.models import (
    AudioMode,
    Container,
    FallbackMode,
    JobError,
    JobOptions,
    TargetCodec,
)
from videocompress.profiles import (
    default_profile_family_name,
    default_profile_name,
    encoding_guide_text,
    get_profile,
    profile_family_names,
    profile_names,
)
from videocompress.quality import default_threshold_for_metric
from videocompress.reporting import write_report
from videocompress.transcode import run_job

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEARCH_PRESET_MAP: dict[str, list[str]] = {
    "all": [],
    "p7": ["p7"],
    "p6": ["p6"],
    "p5": ["p5"],
    "p7, p6": ["p7", "p6"],
    "p7, p6, p5": ["p7", "p6", "p5"],
    "slow": ["slow"],
    "slower": ["slower"],
    "slow, slower": ["slow", "slower"],
}
"""Map GUI dropdown labels to the preset list expected by the optimizer."""

_DEFAULT_QUALITY_THRESHOLDS: dict[str, float] = {
    metric: default_threshold_for_metric(metric) for metric in ("vmaf", "ssim", "psnr")
}
"""Sensible default quality thresholds per metric."""

_INPUT_TAB_VIDEO = "Video File"
_INPUT_TAB_FOLDER = "Folder"

_ENC_TAB_AUTO = "Auto Encoding (recommended)"
_ENC_TAB_MANUAL = "Manual Encoding"
_ENC_TAB_CAMERA = "Camera Footage Profiles"

_PRESET_VALUES = ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "fast", "medium", "slow"]
_RC_MODE_VALUES = ["auto", "vbr", "constqp", "cbr", "crf"]


@dataclass(slots=True)
class _RunConfig:
    auto_search: bool
    metric: str
    threshold: float
    rc_mode: str
    search_presets: list[str]
    preset: str
    quality: int
    validate_quality: bool
    mode_label: str

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class VideoCompressApp(ctk.CTk):
    """Main application window for videocompress.

    The UI is built in ``_build_ui`` and split into logical sections
    (header, input, encoding, options, output, actions, console).
    Encoding jobs run in a daemon thread that posts messages to a
    ``queue.Queue``; the main thread polls the queue via ``after()``
    to keep the UI responsive.
    """

    # Width of the left-side labels (for alignment)
    _LABEL_WIDTH = 120

    def __init__(self) -> None:
        super().__init__()

        self.title(f"videocompress {__version__} — GPU Video Compressor")
        self.geometry("960x860")
        self.minsize(820, 720)

        # Dark theme
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Inter-thread communication
        self._message_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._job_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False

        self._build_ui()
        self._poll_queue()

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Construct the entire window layout."""
        self._main = ctk.CTkScrollableFrame(self)
        self._main.pack(fill="both", expand=True, padx=10, pady=10)
        self._main.columnconfigure(0, weight=1)

        self._build_header()
        self._build_input_section()
        self._build_encoding_section()
        self._build_options_section()
        self._build_output_section()
        self._build_action_section()
        self._build_console_section()

    # -- Header --------------------------------------------------------

    def _build_header(self) -> None:
        ctk.CTkLabel(
            self._main,
            text="videocompress",
            font=ctk.CTkFont(size=24, weight="bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 2))

        ctk.CTkLabel(
            self._main,
            text=(
                "GPU-accelerated visually-lossless video compressor  "
                "(H.264 \u2192 H.265 / AV1 via NVENC)"
            ),
            font=ctk.CTkFont(size=12),
            text_color="gray",
        ).grid(row=1, column=0, sticky="w", pady=(0, 15))

    # -- Input ---------------------------------------------------------

    def _build_input_section(self) -> None:
        sec = self._section(row=2, title="Input")
        sec.columnconfigure(0, weight=1)

        self._input_file = ctk.StringVar()
        self._input_dir = ctk.StringVar()
        self._recursive = ctk.BooleanVar(value=False)

        tabs = ctk.CTkTabview(sec, height=150)
        tabs.grid(row=1, column=0, sticky="ew", padx=10, pady=(3, 10))
        self._input_tabs = tabs

        tab_video = tabs.add(_INPUT_TAB_VIDEO)
        tab_video.columnconfigure(1, weight=1)
        ctk.CTkLabel(tab_video, text="Video File:", width=self._LABEL_WIDTH, anchor="w").grid(
            row=0, column=0, sticky="w", padx=5, pady=8
        )
        ctk.CTkEntry(tab_video, textvariable=self._input_file).grid(
            row=0, column=1, sticky="ew", padx=5, pady=8
        )
        ctk.CTkButton(tab_video, text="Browse", width=80, command=self._browse_file).grid(
            row=0, column=2, padx=5, pady=8
        )

        tab_folder = tabs.add(_INPUT_TAB_FOLDER)
        tab_folder.columnconfigure(1, weight=1)
        ctk.CTkLabel(tab_folder, text="Input Folder:", width=self._LABEL_WIDTH, anchor="w").grid(
            row=0, column=0, sticky="w", padx=5, pady=8
        )
        ctk.CTkEntry(tab_folder, textvariable=self._input_dir).grid(
            row=0, column=1, sticky="ew", padx=5, pady=8
        )
        ctk.CTkButton(tab_folder, text="Browse", width=80, command=self._browse_dir).grid(
            row=0, column=2, padx=5, pady=8
        )
        ctk.CTkCheckBox(
            tab_folder,
            text="Recursive folder scan",
            variable=self._recursive,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=5, pady=(0, 8))

        tabs.set(_INPUT_TAB_VIDEO)

    # -- Encoding ------------------------------------------------------

    def _build_encoding_section(self) -> None:
        sec = self._section(row=3, title="Encoding")

        # Row of dropdowns
        row = ctk.CTkFrame(sec, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=10, pady=3)

        self._codec = ctk.StringVar(value="auto")
        self._container = ctk.StringVar(value="mkv")
        self._audio = ctk.StringVar(value="copy")
        self._fallback = ctk.StringVar(value="fallback-codec")

        for label, var, opts, width in [
            ("Codec:", self._codec, ["auto", "hevc", "av1"], 100),
            ("Container:", self._container, ["mkv", "mp4"], 80),
            ("Audio:", self._audio, ["copy", "aac", "opus"], 80),
            ("GPU Fallback:", self._fallback, ["fail-fast", "fallback-codec", "fallback-cpu"], 150),
        ]:
            ctk.CTkLabel(row, text=label).pack(side="left", padx=(0, 4))
            ctk.CTkOptionMenu(row, variable=var, values=opts, width=width).pack(
                side="left", padx=(0, 14)
            )

        # Tabbed auto / manual / camera profiles
        tabs = ctk.CTkTabview(sec, height=360)
        tabs.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
        self._tabs = tabs

        # ── Auto tab ──
        auto = tabs.add(_ENC_TAB_AUTO)
        auto.columnconfigure(1, weight=1)

        ctk.CTkLabel(auto, text="Search Presets:").grid(row=0, column=0, sticky="w", padx=5, pady=3)
        self._search_presets = ctk.StringVar(value="all")
        ctk.CTkOptionMenu(
            auto,
            variable=self._search_presets,
            values=list(_SEARCH_PRESET_MAP.keys()),
            width=160,
        ).grid(row=0, column=1, sticky="w", padx=5, pady=3)

        ctk.CTkLabel(auto, text="Quality Metric:").grid(row=1, column=0, sticky="w", padx=5, pady=3)
        self._metric = ctk.StringVar(value="vmaf")
        ctk.CTkOptionMenu(
            auto, variable=self._metric, values=["vmaf", "ssim", "psnr"], width=160
        ).grid(row=1, column=1, sticky="w", padx=5, pady=3)

        ctk.CTkLabel(auto, text="Rate Control:").grid(row=2, column=0, sticky="w", padx=5, pady=3)
        self._auto_rc_mode = ctk.StringVar(value="vbr")
        ctk.CTkOptionMenu(
            auto,
            variable=self._auto_rc_mode,
            values=_RC_MODE_VALUES,
            width=160,
        ).grid(row=2, column=1, sticky="w", padx=5, pady=3)

        ctk.CTkLabel(auto, text="Threshold:").grid(row=3, column=0, sticky="w", padx=5, pady=3)
        self._threshold = ctk.StringVar(value="")
        ctk.CTkEntry(
            auto,
            textvariable=self._threshold,
            placeholder_text="Auto (VMAF=95 · SSIM=0.97 · PSNR=40)",
            width=320,
        ).grid(row=3, column=1, sticky="w", padx=5, pady=3)

        self._validate = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(auto, text="Post-encode quality validation", variable=self._validate).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=5, pady=3
        )

        # ── Manual tab ──
        manual = tabs.add(_ENC_TAB_MANUAL)
        manual.columnconfigure(1, weight=1)

        ctk.CTkLabel(manual, text="Preset:").grid(row=0, column=0, sticky="w", padx=5, pady=3)
        self._preset = ctk.StringVar(value="p5")
        ctk.CTkOptionMenu(
            manual,
            variable=self._preset,
            values=_PRESET_VALUES,
            width=160,
        ).grid(row=0, column=1, sticky="w", padx=5, pady=3)

        ctk.CTkLabel(manual, text="Quality (CQ/CRF):").grid(
            row=1, column=0, sticky="w", padx=5, pady=3
        )
        self._quality = ctk.IntVar(value=22)
        qf = ctk.CTkFrame(manual, fg_color="transparent")
        qf.grid(row=1, column=1, sticky="w", padx=5, pady=3)
        ctk.CTkSlider(
            qf, from_=0, to=51, variable=self._quality, width=220, command=self._on_quality_slide
        ).pack(side="left")
        self._quality_label = ctk.CTkLabel(qf, text="22", width=35)
        self._quality_label.pack(side="left", padx=(10, 0))

        ctk.CTkLabel(manual, text="Rate Control:").grid(row=2, column=0, sticky="w", padx=5, pady=3)
        self._manual_rc_mode = ctk.StringVar(value="vbr")
        ctk.CTkOptionMenu(
            manual,
            variable=self._manual_rc_mode,
            values=_RC_MODE_VALUES,
            width=160,
        ).grid(row=2, column=1, sticky="w", padx=5, pady=3)

        # ── Camera tab ──
        camera = tabs.add(_ENC_TAB_CAMERA)
        camera.columnconfigure(1, weight=1)

        ctk.CTkLabel(camera, text="Profile Family:").grid(
            row=0,
            column=0,
            sticky="w",
            padx=5,
            pady=3,
        )
        self._camera_profile_family = ctk.StringVar(value=default_profile_family_name())
        ctk.CTkOptionMenu(
            camera,
            variable=self._camera_profile_family,
            values=profile_family_names(),
            command=self._on_camera_family_changed,
            width=320,
        ).grid(row=0, column=1, sticky="w", padx=5, pady=3)

        ctk.CTkLabel(camera, text="Profile:").grid(row=1, column=0, sticky="w", padx=5, pady=3)
        camera_family = self._camera_profile_family.get()
        self._camera_profile_name = ctk.StringVar(value=default_profile_name(camera_family))
        self._camera_profile_menu = ctk.CTkOptionMenu(
            camera,
            variable=self._camera_profile_name,
            values=profile_names(camera_family),
            command=self._on_camera_profile_changed,
            width=320,
        )
        self._camera_profile_menu.grid(row=1, column=1, sticky="w", padx=5, pady=3)

        self._camera_profile_desc = ctk.CTkLabel(
            camera,
            text="",
            wraplength=560,
            justify="left",
            text_color="gray",
        )
        self._camera_profile_desc.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="w",
            padx=5,
            pady=(0, 6),
        )

        self._camera_apply_codec_container = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            camera,
            text="Apply profile codec/container defaults",
            variable=self._camera_apply_codec_container,
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=5, pady=3)

        ctk.CTkLabel(camera, text="Preset:").grid(row=4, column=0, sticky="w", padx=5, pady=3)
        self._camera_preset = ctk.StringVar(value="p7")
        ctk.CTkOptionMenu(
            camera,
            variable=self._camera_preset,
            values=_PRESET_VALUES,
            width=160,
        ).grid(row=4, column=1, sticky="w", padx=5, pady=3)

        ctk.CTkLabel(camera, text="Quality Factor:").grid(
            row=5,
            column=0,
            sticky="w",
            padx=5,
            pady=3,
        )
        self._camera_quality = ctk.IntVar(value=24)
        cqf = ctk.CTkFrame(camera, fg_color="transparent")
        cqf.grid(row=5, column=1, sticky="w", padx=5, pady=3)
        ctk.CTkSlider(
            cqf,
            from_=0,
            to=51,
            variable=self._camera_quality,
            width=220,
            command=self._on_camera_quality_slide,
        ).pack(side="left")
        self._camera_quality_label = ctk.CTkLabel(cqf, text="24", width=35)
        self._camera_quality_label.pack(side="left", padx=(10, 0))

        ctk.CTkLabel(camera, text="Rate Control:").grid(row=6, column=0, sticky="w", padx=5, pady=3)
        self._camera_rc_mode = ctk.StringVar(value="vbr")
        ctk.CTkOptionMenu(
            camera,
            variable=self._camera_rc_mode,
            values=_RC_MODE_VALUES,
            width=160,
        ).grid(row=6, column=1, sticky="w", padx=5, pady=3)

        ctk.CTkLabel(camera, text="Quality Metric:").grid(
            row=7,
            column=0,
            sticky="w",
            padx=5,
            pady=3,
        )
        self._camera_metric = ctk.StringVar(value="vmaf")
        ctk.CTkOptionMenu(
            camera,
            variable=self._camera_metric,
            values=["vmaf", "ssim", "psnr"],
            width=160,
        ).grid(row=7, column=1, sticky="w", padx=5, pady=3)

        ctk.CTkLabel(camera, text="Threshold:").grid(row=8, column=0, sticky="w", padx=5, pady=3)
        self._camera_threshold = ctk.StringVar(value="")
        ctk.CTkEntry(
            camera,
            textvariable=self._camera_threshold,
            placeholder_text="Auto based on metric",
            width=220,
        ).grid(row=8, column=1, sticky="w", padx=5, pady=3)

        self._camera_validate = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            camera,
            text="Post-encode quality validation",
            variable=self._camera_validate,
        ).grid(row=9, column=0, columnspan=2, sticky="w", padx=5, pady=(3, 8))

        ctk.CTkLabel(
            camera,
            text=encoding_guide_text(),
            wraplength=640,
            justify="left",
            text_color="gray",
        ).grid(row=10, column=0, columnspan=2, sticky="w", padx=5, pady=(0, 8))

        self._apply_camera_profile(camera_family, self._camera_profile_name.get())
        tabs.set(_ENC_TAB_AUTO)

    # -- Options -------------------------------------------------------

    def _build_options_section(self) -> None:
        sec = self._section(row=4, title="Options")

        row = ctk.CTkFrame(sec, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))

        self._gpu = ctk.BooleanVar(value=True)
        self._lossless = ctk.BooleanVar(value=False)
        self._overwrite = ctk.BooleanVar(value=False)
        self._dryrun = ctk.BooleanVar(value=False)

        for text, var in [
            ("GPU Acceleration (NVENC)", self._gpu),
            ("Lossless Mode", self._lossless),
            ("Overwrite Existing", self._overwrite),
            ("Dry Run", self._dryrun),
        ]:
            ctk.CTkCheckBox(row, text=text, variable=var).pack(side="left", padx=(0, 16))

    # -- Output --------------------------------------------------------

    def _build_output_section(self) -> None:
        sec = self._section(row=5, title="Output")
        sec.columnconfigure(1, weight=1)

        ctk.CTkLabel(sec, text="Output Directory:", width=self._LABEL_WIDTH, anchor="w").grid(
            row=1, column=0, sticky="w", padx=10, pady=3
        )
        self._output_dir = ctk.StringVar(value="./output")
        ctk.CTkEntry(sec, textvariable=self._output_dir).grid(
            row=1, column=1, sticky="ew", padx=5, pady=3
        )
        ctk.CTkButton(sec, text="Browse", width=80, command=self._browse_output).grid(
            row=1, column=2, padx=10, pady=3
        )

        ctk.CTkLabel(sec, text="JSON Report:", width=self._LABEL_WIDTH, anchor="w").grid(
            row=2, column=0, sticky="w", padx=10, pady=3
        )
        self._report = ctk.StringVar(value="")
        ctk.CTkEntry(
            sec, textvariable=self._report, placeholder_text="Optional — save detailed report"
        ).grid(row=2, column=1, sticky="ew", padx=5, pady=(3, 10))
        ctk.CTkButton(sec, text="Browse", width=80, command=self._browse_report).grid(
            row=2, column=2, padx=10, pady=(3, 10)
        )

    # -- Action buttons + progress -------------------------------------

    def _build_action_section(self) -> None:
        af = ctk.CTkFrame(self._main, fg_color="transparent")
        af.grid(row=6, column=0, sticky="ew", pady=(0, 5))
        af.columnconfigure(0, weight=3)
        af.columnconfigure(1, weight=1)

        self._start_btn = ctk.CTkButton(
            af,
            text="\u25b6  Start Compression",
            height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_job,
        )
        self._start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        self._stop_btn = ctk.CTkButton(
            af,
            text="\u25a0  Stop",
            height=42,
            font=ctk.CTkFont(size=14),
            fg_color="#c0392b",
            hover_color="#922b21",
            command=self._stop_job,
            state="disabled",
        )
        self._stop_btn.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        # Progress bar
        self._progress = ctk.CTkProgressBar(self._main)
        self._progress.grid(row=7, column=0, sticky="ew", pady=(5, 2))
        self._progress.set(0)

        self._status = ctk.CTkLabel(self._main, text="Ready", font=ctk.CTkFont(size=11))
        self._status.grid(row=8, column=0, sticky="w", pady=(0, 5))

    # -- Console output ------------------------------------------------

    def _build_console_section(self) -> None:
        self._console = ctk.CTkTextbox(
            self._main, height=260, font=ctk.CTkFont(family="Consolas", size=11)
        )
        self._console.grid(row=9, column=0, sticky="nsew", pady=(0, 5))
        self._main.rowconfigure(9, weight=1)
        self._console.configure(state="disabled")

    # ── Helpers ───────────────────────────────────────────────────────

    def _section(self, *, row: int, title: str) -> ctk.CTkFrame:
        """Create a labelled section frame at the given grid row."""
        frame = ctk.CTkFrame(self._main)
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        frame.columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 5)
        )
        return frame

    def _console_write(self, text: str) -> None:
        """Append a line to the console textbox (main-thread only)."""
        self._console.configure(state="normal")
        self._console.insert("end", text + "\n")
        self._console.see("end")
        self._console.configure(state="disabled")

    def _console_clear(self) -> None:
        """Clear all console text."""
        self._console.configure(state="normal")
        self._console.delete("1.0", "end")
        self._console.configure(state="disabled")

    # ── Browse dialogs ────────────────────────────────────────────────

    def _browse_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Video File",
            filetypes=video_filetypes(),
        )
        if path:
            self._input_file.set(path)
            self._input_tabs.set(_INPUT_TAB_VIDEO)

    def _browse_dir(self) -> None:
        path = filedialog.askdirectory(title="Select Input Folder")
        if path:
            self._input_dir.set(path)
            self._input_tabs.set(_INPUT_TAB_FOLDER)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Select Output Directory")
        if path:
            self._output_dir.set(path)

    def _browse_report(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save JSON Report",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self._report.set(path)

    # ── Slider callback ──────────────────────────────────────────────

    def _on_quality_slide(self, value: float) -> None:
        """Update the quality numeric label next to the slider."""
        self._quality_label.configure(text=str(int(value)))

    def _on_camera_quality_slide(self, value: float) -> None:
        """Update the camera quality numeric label next to the slider."""
        self._camera_quality_label.configure(text=str(int(value)))

    def _on_camera_family_changed(self, selected_family: str) -> None:
        """Refresh profile list when family changes and apply the default profile."""
        names = profile_names(selected_family)
        if not names:
            return
        self._camera_profile_menu.configure(values=names)
        profile_name = default_profile_name(selected_family)
        if profile_name not in names:
            profile_name = names[0]
        self._camera_profile_name.set(profile_name)
        self._apply_camera_profile(selected_family, profile_name)

    def _on_camera_profile_changed(self, selected_name: str) -> None:
        """Apply selected camera profile defaults to camera controls."""
        self._apply_camera_profile(self._camera_profile_family.get(), selected_name)

    def _apply_camera_profile(self, family_name: str, profile_name: str) -> None:
        """Apply camera profile defaults to GUI controls."""
        profile = get_profile(family_name, profile_name)

        self._camera_preset.set(profile.preset)
        self._camera_quality.set(profile.quality)
        self._camera_quality_label.configure(text=str(profile.quality))
        self._camera_rc_mode.set(profile.rc_mode)
        self._camera_metric.set(profile.quality_metric)
        self._camera_threshold.set(str(profile.quality_threshold))
        self._camera_validate.set(profile.validate_quality)
        self._camera_profile_desc.configure(text=profile.description)

        if self._camera_apply_codec_container.get():
            self._codec.set(profile.codec.value)
            self._container.set(profile.container.value)

    def _parse_threshold(self, metric: str, threshold_text: str) -> float | None:
        """Parse threshold text, falling back to metric defaults."""
        text = threshold_text.strip()
        if not text:
            return _DEFAULT_QUALITY_THRESHOLDS.get(metric, default_threshold_for_metric(metric))
        try:
            return float(text)
        except ValueError:
            messagebox.showerror(
                "Invalid Threshold",
                f"Threshold must be numeric. Received: {threshold_text}",
            )
            return None

    def _build_run_config(self) -> _RunConfig | None:
        """Build a runtime configuration based on the active encoding tab."""
        encoding_tab = self._tabs.get()

        if encoding_tab == _ENC_TAB_AUTO:
            metric = self._metric.get()
            threshold = self._parse_threshold(metric, self._threshold.get())
            if threshold is None:
                return None
            return _RunConfig(
                auto_search=True,
                metric=metric,
                threshold=threshold,
                rc_mode=self._auto_rc_mode.get(),
                search_presets=_SEARCH_PRESET_MAP.get(self._search_presets.get(), []),
                preset=self._preset.get(),
                quality=self._quality.get(),
                validate_quality=self._validate.get(),
                mode_label="AUTO-SEARCH",
            )

        if encoding_tab == _ENC_TAB_MANUAL:
            metric = self._metric.get()
            threshold = self._parse_threshold(metric, self._threshold.get())
            if threshold is None:
                return None
            return _RunConfig(
                auto_search=False,
                metric=metric,
                threshold=threshold,
                rc_mode=self._manual_rc_mode.get(),
                search_presets=[],
                preset=self._preset.get(),
                quality=self._quality.get(),
                validate_quality=self._validate.get(),
                mode_label="MANUAL",
            )

        if encoding_tab == _ENC_TAB_CAMERA:
            profile = get_profile(
                self._camera_profile_family.get(),
                self._camera_profile_name.get(),
            )
            if self._camera_apply_codec_container.get():
                self._codec.set(profile.codec.value)
                self._container.set(profile.container.value)

            metric = self._camera_metric.get()
            threshold = self._parse_threshold(metric, self._camera_threshold.get())
            if threshold is None:
                return None
            return _RunConfig(
                auto_search=False,
                metric=metric,
                threshold=threshold,
                rc_mode=self._camera_rc_mode.get(),
                search_presets=[],
                preset=self._camera_preset.get(),
                quality=self._camera_quality.get(),
                validate_quality=self._camera_validate.get(),
                mode_label=(
                    "CAMERA PROFILE "
                    f"[{self._camera_profile_family.get()} / {profile.name}]"
                ),
            )

        messagebox.showerror("Invalid Encoding Mode", f"Unknown encoding mode: {encoding_tab}")
        return None

    def _collect_files_from_folder(self, input_dir: Path) -> list[Path] | None:
        """Collect files from a folder and show user-facing errors if needed."""
        if not input_dir.exists() or not input_dir.is_dir():
            messagebox.showerror("Not Found", f"Input folder not found:\n{input_dir}")
            return None

        files = collect_video_files(input_dir, self._recursive.get(), probe_unknown=True)
        if not files:
            messagebox.showerror("No Videos", "No video files found in the selected folder.")
            return None
        return files

    def _resolve_input_files(self) -> list[Path] | None:
        """Resolve input files according to the selected input mode."""
        input_mode = self._input_tabs.get().strip() or _INPUT_TAB_VIDEO
        input_file = self._input_file.get().strip()
        input_dir = self._input_dir.get().strip()

        if input_mode == _INPUT_TAB_VIDEO:
            if not input_file:
                messagebox.showerror(
                    "Input Required",
                    "Select an input video file or switch to Folder mode.",
                )
                return None

            source = Path(input_file)
            if source.is_dir():
                messagebox.showerror(
                    "Invalid Input",
                    "The selected video path is a folder. Use Folder mode instead.",
                )
                return None
            if not source.exists() or not source.is_file():
                messagebox.showerror("Not Found", f"Input file not found:\n{source}")
                return None
            return [source]

        if input_mode == _INPUT_TAB_FOLDER:
            if not input_dir:
                messagebox.showerror(
                    "Input Required",
                    "Select an input folder or switch to Video File mode.",
                )
                return None
            return self._collect_files_from_folder(Path(input_dir))

        messagebox.showerror("Invalid Input Mode", f"Unknown input mode: {input_mode}")
        return None

    # ── Job lifecycle ────────────────────────────────────────────────

    def _start_job(self) -> None:
        """Validate inputs, build file list, and launch the worker thread."""
        if self._running:
            return

        # --- Resolve input files ---
        files = self._resolve_input_files()
        if not files:
            return

        # --- Gather settings ---
        run_config = self._build_run_config()
        if run_config is None:
            return

        output_dir = Path(self._output_dir.get().strip() or "./output")
        report_json = Path(self._report.get().strip()) if self._report.get().strip() else None

        # --- Prepare UI ---
        self._running = True
        self._stop_event.clear()
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._progress.set(0)
        self._console_clear()

        self._console_write(f"{'=' * 60}")
        self._console_write(f"  Mode:    {run_config.mode_label}")
        self._console_write(f"  Input:   {self._input_tabs.get()}")
        self._console_write(f"  Files:   {len(files)}")
        codec = self._codec.get()
        ctr = self._container.get()
        self._console_write(f"  Codec:   {codec}  |  Container: {ctr}")
        if run_config.auto_search:
            self._console_write(
                f"  Metric:  {run_config.metric}  |  "
                f"Threshold: {run_config.threshold}  |  RC: {run_config.rc_mode}"
            )
        else:
            self._console_write(
                f"  Preset:  {run_config.preset}  |  "
                f"Quality: {run_config.quality}  |  RC: {run_config.rc_mode}"
            )
        self._console_write(f"{'=' * 60}\n")

        # --- Launch worker ---
        self._job_thread = threading.Thread(
            target=self._worker,
            args=(
                files,
                run_config,
                output_dir,
                report_json,
            ),
            daemon=True,
        )
        self._job_thread.start()

    def _stop_job(self) -> None:
        """Signal the worker thread to stop at the next checkpoint."""
        if self._running:
            self._stop_event.set()
            self._console_write("\u23f9 Stopping\u2026")

    # ── Worker thread ────────────────────────────────────────────────

    def _worker(
        self,
        files: list[Path],
        run_config: _RunConfig,
        output_dir: Path,
        report_json: Path | None,
    ) -> None:
        """Run compression jobs in a background thread.

        All UI updates are posted to ``self._message_queue`` and applied
        by the main thread in ``_poll_queue``.
        """
        q = self._message_queue
        outcomes: list = []
        failed = 0

        for idx, file_path in enumerate(files, start=1):
            if self._stop_event.is_set():
                q.put(("log", "Cancelled by user."))
                break

            q.put(("log", f"\n[{idx}/{len(files)}] Processing: {file_path.name}"))

            # Progress callback — invoked from the transcode engine
            def _progress(pct: float, msg: str, _i: int = idx) -> None:
                if pct < 0:
                    q.put(("log", msg))
                else:
                    global_pct = ((_i - 1) + pct / 100.0) / len(files)
                    q.put(("progress", global_pct))
                    q.put(("status", msg))

            per_report = report_json if (report_json and len(files) == 1) else None

            opts = JobOptions(
                input_path=file_path,
                output_dir=output_dir,
                output_container=Container(self._container.get()),
                codec=TargetCodec(self._codec.get()),
                fallback_mode=FallbackMode(self._fallback.get()),
                preset=run_config.preset,
                quality=run_config.quality,
                quality_mode=run_config.auto_search,
                validate_quality=run_config.validate_quality,
                quality_metric=run_config.metric,
                quality_threshold=run_config.threshold,
                rc_mode=run_config.rc_mode,
                auto_search_best=run_config.auto_search,
                enable_gpu_optimization=self._gpu.get(),
                dry_run=self._dryrun.get(),
                overwrite=self._overwrite.get(),
                audio_mode=AudioMode(self._audio.get()),
                report_json=per_report,
                lossless=self._lossless.get(),
                search_presets=run_config.search_presets,
            )

            try:
                outcome = run_job(opts, _progress, self._stop_event)
                outcomes.append(outcome)
            except JobError as exc:
                failed += 1
                q.put(("log", f"ERROR [{exc.taxonomy}] {file_path.name}: {exc}"))

        # ── Results summary ──
        if outcomes:
            q.put(("log", f"\n{'=' * 60}"))
            q.put(("log", "  RESULTS"))
            q.put(("log", f"{'=' * 60}"))

            total_in = total_out = 0
            for outcome in outcomes:
                in_mb = outcome.input_size / 1_048_576
                out_mb = outcome.output_size / 1_048_576
                savings = (1.0 - outcome.compression_ratio) * 100.0
                total_in += outcome.input_size
                total_out += outcome.output_size

                if self._dryrun.get():
                    q.put(("log", f"  {outcome.input_path.name}: DRY RUN"))
                elif outcome.copied_original:
                    q.put(
                        (
                            "log",
                            f"  {outcome.input_path.name}: kept original "
                            "(encoded file was larger; conversion skipped)",
                        )
                    )
                else:
                    q.put(
                        (
                            "log",
                            f"  {outcome.input_path.name}: "
                            f"{in_mb:.1f} MB \u2192 {out_mb:.1f} MB  ({savings:+.1f}%)",
                        )
                    )

                if outcome.optimization and outcome.optimization.similarity_value is not None:
                    opt = outcome.optimization
                    q.put(
                        (
                            "log",
                            f"    preset={opt.preset}  CQ={opt.quality}  "
                            f"rc={opt.rc_mode}  {opt.bit_depth}-bit  "
                            f"{run_config.metric}={opt.similarity_value:.2f}",
                        )
                    )

            if len(outcomes) > 1 and total_in > 0:
                ratio = (1.0 - total_out / total_in) * 100.0
                q.put(
                    (
                        "log",
                        f"  TOTAL: {total_in / 1_048_576:.1f} MB \u2192 "
                        f"{total_out / 1_048_576:.1f} MB  ({ratio:+.1f}%)",
                    )
                )

            # Write reports
            if report_json and not self._dryrun.get():
                try:
                    if len(outcomes) == 1:
                        write_report(report_json, outcomes[0])
                        q.put(("log", f"  Report: {report_json}"))
                    else:
                        base = Path(report_json)
                        rdir = base.parent or Path(".")
                        rdir.mkdir(parents=True, exist_ok=True)
                        for oc in outcomes:
                            p = rdir / f"{base.stem}.{oc.input_path.stem}.json"
                            write_report(p, oc)
                        q.put(("log", f"  Reports saved to: {rdir}"))
                except Exception as exc:
                    q.put(("log", f"  WARNING: Report write failed: {exc}"))

            q.put(("log", f"{'=' * 60}"))

        q.put(("progress", 1.0))
        q.put(("done", failed))

    # ── Queue polling (main thread) ──────────────────────────────────

    def _poll_queue(self) -> None:
        """Drain the message queue and update UI widgets.

        Called every 100 ms via ``after()``.  Message types:

        - ``("log", str)``      — append text to the console
        - ``("progress", float)`` — set progress bar (0.0 … 1.0)
        - ``("status", str)``   — update the status label
        - ``("done", int)``     — job finished; ``int`` = number of failures
        """
        try:
            while True:
                kind, data = self._message_queue.get_nowait()
                if kind == "log":
                    self._console_write(str(data))
                elif kind == "progress":
                    self._progress.set(float(data))
                elif kind == "status":
                    self._status.configure(text=str(data))
                elif kind == "done":
                    self._running = False
                    self._start_btn.configure(state="normal")
                    self._stop_btn.configure(state="disabled")
                    n_fail = int(data)
                    if self._stop_event.is_set():
                        self._status.configure(text="Cancelled")
                    elif n_fail:
                        self._status.configure(text=f"Complete with {n_fail} error(s)")
                    else:
                        self._status.configure(text="Complete")
        except queue.Empty:
            pass

        self.after(100, self._poll_queue)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def launch() -> None:
    """Create and run the application main loop.

    This is the entry point for both:
      - ``videocompress gui``   (CLI sub-command)
      - ``videocompress-gui``   (direct script entry-point)
    """
    app = VideoCompressApp()
    app.mainloop()


if __name__ == "__main__":
    launch()

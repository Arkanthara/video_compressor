"""customtkinter GUI for videocompress — GPU-accelerated video compressor.

Provides a modern, dark-themed interface with:
  - File / folder pickers for input selection
  - Codec, container, audio, and fallback dropdowns
  - Tabbed Auto / Manual encoding modes
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
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from videocompress import __version__
from videocompress.models import (
    AudioMode,
    Container,
    FallbackMode,
    JobError,
    JobOptions,
    TargetCodec,
)
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
    "vmaf": 95.0,
    "ssim": 0.97,
    "psnr": 40.0,
}
"""Sensible default quality thresholds per metric."""

_VIDEO_EXTENSIONS = ("*.mp4", "*.mkv", "*.mov", "*.MP4", "*.MKV", "*.MOV")
"""Glob patterns used to discover video files in a folder."""


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
        sec.columnconfigure(1, weight=1)

        # Video file
        ctk.CTkLabel(sec, text="Video File:", width=self._LABEL_WIDTH, anchor="w").grid(
            row=1, column=0, sticky="w", padx=10, pady=3
        )
        self._input_file = ctk.StringVar()
        ctk.CTkEntry(sec, textvariable=self._input_file).grid(
            row=1, column=1, sticky="ew", padx=5, pady=3
        )
        ctk.CTkButton(sec, text="Browse", width=80, command=self._browse_file).grid(
            row=1, column=2, padx=10, pady=3
        )

        # Input folder
        ctk.CTkLabel(sec, text="Input Folder:", width=self._LABEL_WIDTH, anchor="w").grid(
            row=2, column=0, sticky="w", padx=10, pady=3
        )
        self._input_dir = ctk.StringVar()
        ctk.CTkEntry(sec, textvariable=self._input_dir).grid(
            row=2, column=1, sticky="ew", padx=5, pady=3
        )
        ctk.CTkButton(sec, text="Browse", width=80, command=self._browse_dir).grid(
            row=2, column=2, padx=10, pady=3
        )

        # Recursive
        self._recursive = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(sec, text="Recursive folder scan", variable=self._recursive).grid(
            row=3, column=0, columnspan=3, sticky="w", padx=10, pady=(3, 10)
        )

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

        # Tabbed auto / manual
        tabs = ctk.CTkTabview(sec, height=170)
        tabs.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
        self._tabs = tabs

        # ── Auto tab ──
        auto = tabs.add("Auto Encoding (recommended)")
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

        ctk.CTkLabel(auto, text="Threshold:").grid(row=2, column=0, sticky="w", padx=5, pady=3)
        self._threshold = ctk.StringVar(value="")
        ctk.CTkEntry(
            auto,
            textvariable=self._threshold,
            placeholder_text="Auto (VMAF=95 · SSIM=0.97 · PSNR=40)",
            width=320,
        ).grid(row=2, column=1, sticky="w", padx=5, pady=3)

        self._validate = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(auto, text="Post-encode quality validation", variable=self._validate).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=5, pady=3
        )

        # ── Manual tab ──
        manual = tabs.add("Manual Encoding")
        manual.columnconfigure(1, weight=1)

        ctk.CTkLabel(manual, text="Preset:").grid(row=0, column=0, sticky="w", padx=5, pady=3)
        self._preset = ctk.StringVar(value="p5")
        ctk.CTkOptionMenu(
            manual,
            variable=self._preset,
            values=["p1", "p2", "p3", "p4", "p5", "p6", "p7", "fast", "medium", "slow"],
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

        tabs.set("Auto Encoding (recommended)")

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
            filetypes=[("Video files", "*.mp4 *.mkv *.mov"), ("All files", "*.*")],
        )
        if path:
            self._input_file.set(path)

    def _browse_dir(self) -> None:
        path = filedialog.askdirectory(title="Select Input Folder")
        if path:
            self._input_dir.set(path)

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

    # ── Job lifecycle ────────────────────────────────────────────────

    def _start_job(self) -> None:
        """Validate inputs, build file list, and launch the worker thread."""
        if self._running:
            return

        # --- Resolve input files ---
        input_file = self._input_file.get().strip()
        input_dir = self._input_dir.get().strip()

        if not input_file and not input_dir:
            messagebox.showerror("Input Required", "Select an input video file or folder.")
            return

        if input_file:
            source = Path(input_file)
            if source.is_dir():
                input_dir = input_file
                input_file = ""
            elif not source.exists() or not source.is_file():
                messagebox.showerror("Not Found", f"Input file not found:\n{source}")
                return

        if input_file:
            files = [Path(input_file)]
        else:
            source = Path(input_dir)
            if not source.exists() or not source.is_dir():
                messagebox.showerror("Not Found", f"Input folder not found:\n{source}")
                return
            files: list[Path] = []
            for pat in _VIDEO_EXTENSIONS:
                if self._recursive.get():
                    files.extend(source.rglob(pat))
                else:
                    files.extend(source.glob(pat))
            files = sorted({p for p in files if p.is_file()})
            if not files:
                messagebox.showerror("No Videos", "No video files found in the selected folder.")
                return

        # --- Gather settings ---
        auto_search = self._tabs.get() == "Auto Encoding (recommended)"
        metric = self._metric.get()
        threshold_text = self._threshold.get().strip()
        threshold = (
            float(threshold_text)
            if threshold_text
            else _DEFAULT_QUALITY_THRESHOLDS.get(metric, 95.0)
        )
        search_presets = _SEARCH_PRESET_MAP.get(self._search_presets.get(), [])
        output_dir = Path(self._output_dir.get().strip() or "./output")
        report_json = Path(self._report.get().strip()) if self._report.get().strip() else None

        # --- Prepare UI ---
        self._running = True
        self._stop_event.clear()
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._progress.set(0)
        self._console_clear()

        mode = "AUTO-SEARCH" if auto_search else "MANUAL"
        self._console_write(f"{'=' * 60}")
        self._console_write(f"  Mode:    {mode}")
        self._console_write(f"  Files:   {len(files)}")
        codec = self._codec.get()
        ctr = self._container.get()
        self._console_write(f"  Codec:   {codec}  |  Container: {ctr}")
        if auto_search:
            self._console_write(f"  Metric:  {metric}  |  Threshold: {threshold}")
        else:
            self._console_write(
                f"  Preset:  {self._preset.get()}  |  Quality: {self._quality.get()}"
            )
        self._console_write(f"{'=' * 60}\n")

        # --- Launch worker ---
        self._job_thread = threading.Thread(
            target=self._worker,
            args=(
                files,
                auto_search,
                metric,
                threshold,
                search_presets,
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
        auto_search: bool,
        metric: str,
        threshold: float,
        search_presets: list[str],
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
                preset=self._preset.get(),
                quality=self._quality.get(),
                quality_mode=auto_search,
                validate_quality=self._validate.get(),
                quality_metric=metric,
                quality_threshold=threshold,
                auto_search_best=auto_search,
                enable_gpu_optimization=self._gpu.get(),
                dry_run=self._dryrun.get(),
                overwrite=self._overwrite.get(),
                audio_mode=AudioMode(self._audio.get()),
                report_json=per_report,
                lossless=self._lossless.get(),
                search_presets=search_presets,
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
                            f"{metric}={opt.similarity_value:.2f}",
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

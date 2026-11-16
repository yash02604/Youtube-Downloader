"""
Download scheduling system for delayed and timed downloads
"""
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta
import threading
import time
from typing import Dict, List, Optional, Callable
import json
import logging
from pathlib import Path
from logging_utils import redact_sensitive_text

logger = logging.getLogger(__name__)


class ScheduledDownload:
    """Represents a scheduled download"""
    
    def __init__(self, url: str, download_path: str, scheduled_time: datetime, 
                 download_options: Dict = None, schedule_id: str = None):
        self.schedule_id = schedule_id or f"sch_{int(time.time() * 1000)}"
        self.url = url
        self.download_path = download_path
        self.scheduled_time = scheduled_time
        self.download_options = download_options or {}
        self.created_time = datetime.now()
        self.status = "Scheduled"  # Scheduled, Active, Completed, Cancelled, Failed
        self.download_id = None  # ID assigned when download starts
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization"""
        return {
            'schedule_id': self.schedule_id,
            'url': self.url,
            'download_path': self.download_path,
            'scheduled_time': self.scheduled_time.isoformat(),
            'download_options': self.download_options,
            'created_time': self.created_time.isoformat(),
            'status': self.status,
            'download_id': self.download_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ScheduledDownload':
        """Create from dictionary"""
        scheduled_time = datetime.fromisoformat(data['scheduled_time'])
        created_time = datetime.fromisoformat(data['created_time'])
        
        instance = cls(
            url=data['url'],
            download_path=data['download_path'],
            scheduled_time=scheduled_time,
            download_options=data.get('download_options', {}),
            schedule_id=data['schedule_id']
        )
        instance.created_time = created_time
        instance.status = data.get('status', 'Scheduled')
        instance.download_id = data.get('download_id')
        return instance


class SchedulerDialog:
    """Dialog for scheduling downloads"""
    
    def __init__(self, parent, url: str, download_path: str, scheduler):
        self.parent = parent
        self.url = url
        self.download_path = download_path
        self.scheduler = scheduler
        
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Schedule Download")
        self.dialog.geometry("400x350")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the scheduling dialog UI"""
        main_frame = ttk.Frame(self.dialog, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # URL display
        ttk.Label(main_frame, text="URL:", font=('Arial', 9, 'bold')).pack(anchor=tk.W)
        url_frame = ttk.Frame(main_frame)
        url_frame.pack(fill=tk.X, pady=(0, 15))
        url_text = tk.Text(url_frame, height=3, wrap=tk.WORD)
        url_text.insert(1.0, self.url)
        url_text.config(state=tk.DISABLED)
        url_text.pack(fill=tk.X)
        
        # Scheduling options
        ttk.Label(main_frame, text="Schedule Type:", font=('Arial', 9, 'bold')).pack(anchor=tk.W)
        
        self.schedule_type = tk.StringVar(value="later")
        type_frame = ttk.Frame(main_frame)
        type_frame.pack(fill=tk.X, pady=(5, 15))
        
        ttk.Radiobutton(type_frame, text="Later today", variable=self.schedule_type, 
                       value="later", command=self.update_time_options).pack(anchor=tk.W)
        ttk.Radiobutton(type_frame, text="Tomorrow", variable=self.schedule_type, 
                       value="tomorrow", command=self.update_time_options).pack(anchor=tk.W)
        ttk.Radiobutton(type_frame, text="Custom date/time", variable=self.schedule_type, 
                       value="custom", command=self.update_time_options).pack(anchor=tk.W)
        
        # Time selection
        ttk.Label(main_frame, text="Time:", font=('Arial', 9, 'bold')).pack(anchor=tk.W)
        
        time_frame = ttk.Frame(main_frame)
        time_frame.pack(fill=tk.X, pady=(5, 15))
        
        # Hour spinbox
        ttk.Label(time_frame, text="Hour:").pack(side=tk.LEFT)
        self.hour_var = tk.IntVar(value=datetime.now().hour)
        hour_spin = ttk.Spinbox(time_frame, from_=0, to=23, textvariable=self.hour_var, width=5)
        hour_spin.pack(side=tk.LEFT, padx=(5, 15))
        
        # Minute spinbox
        ttk.Label(time_frame, text="Minute:").pack(side=tk.LEFT)
        self.minute_var = tk.IntVar(value=(datetime.now().minute // 5) * 5)  # Round to 5-min intervals
        minute_spin = ttk.Spinbox(time_frame, from_=0, to=59, textvariable=self.minute_var, 
                                 width=5, increment=5)
        minute_spin.pack(side=tk.LEFT, padx=(5, 0))
        
        # Date for custom scheduling
        self.date_frame = ttk.Frame(main_frame)
        ttk.Label(self.date_frame, text="Date:", font=('Arial', 9, 'bold')).pack(anchor=tk.W, pady=(10, 0))
        
        date_inner_frame = ttk.Frame(self.date_frame)
        date_inner_frame.pack(fill=tk.X, pady=5)
        
        self.year_var = tk.IntVar(value=datetime.now().year)
        self.month_var = tk.IntVar(value=datetime.now().month)
        self.day_var = tk.IntVar(value=datetime.now().day)
        
        ttk.Label(date_inner_frame, text="Year:").pack(side=tk.LEFT)
        year_spin = ttk.Spinbox(date_inner_frame, from_=datetime.now().year, 
                               to=datetime.now().year + 5, textvariable=self.year_var, width=6)
        year_spin.pack(side=tk.LEFT, padx=(5, 10))
        
        ttk.Label(date_inner_frame, text="Month:").pack(side=tk.LEFT)
        month_spin = ttk.Spinbox(date_inner_frame, from_=1, to=12, 
                                textvariable=self.month_var, width=4)
        month_spin.pack(side=tk.LEFT, padx=(5, 10))
        
        ttk.Label(date_inner_frame, text="Day:").pack(side=tk.LEFT)
        day_spin = ttk.Spinbox(date_inner_frame, from_=1, to=31, 
                              textvariable=self.day_var, width=4)
        day_spin.pack(side=tk.LEFT, padx=(5, 0))
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(15, 0))
        
        ttk.Button(button_frame, text="Schedule", 
                  command=self.schedule_download).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(button_frame, text="Cancel", 
                  command=self.dialog.destroy).pack(side=tk.RIGHT)
        
        # Update initial state
        self.update_time_options()
    
    def update_time_options(self):
        """Update time options based on schedule type"""
        schedule_type = self.schedule_type.get()
        
        if schedule_type == "custom":
            self.date_frame.pack(fill=tk.X, pady=(0, 15))
        else:
            self.date_frame.pack_forget()
    
    def get_scheduled_time(self) -> datetime:
        """Calculate the scheduled time"""
        schedule_type = self.schedule_type.get()
        now = datetime.now()
        
        if schedule_type == "later":
            # Later today
            scheduled_time = now.replace(hour=self.hour_var.get(), minute=self.minute_var.get(), 
                                       second=0, microsecond=0)
            if scheduled_time <= now:
                # If time has passed today, schedule for tomorrow
                scheduled_time += timedelta(days=1)
        elif schedule_type == "tomorrow":
            # Tomorrow at specified time
            scheduled_time = (now + timedelta(days=1)).replace(
                hour=self.hour_var.get(), minute=self.minute_var.get(), 
                second=0, microsecond=0
            )
        else:
            # Custom date/time
            try:
                scheduled_time = datetime(
                    year=self.year_var.get(),
                    month=self.month_var.get(),
                    day=self.day_var.get(),
                    hour=self.hour_var.get(),
                    minute=self.minute_var.get(),
                    second=0,
                    microsecond=0
                )
                if scheduled_time <= now:
                    raise ValueError("Scheduled time must be in the future")
            except ValueError as e:
                raise ValueError(f"Invalid date: {e}")
        
        return scheduled_time
    
    def schedule_download(self):
        """Schedule the download"""
        try:
            scheduled_time = self.get_scheduled_time()
            
            # Create scheduled download
            scheduled_download = ScheduledDownload(
                url=self.url,
                download_path=self.download_path,
                scheduled_time=scheduled_time
            )
            
            # Add to scheduler
            self.scheduler.add_scheduled_download(scheduled_download)
            
            # Show confirmation
            time_str = scheduled_time.strftime("%Y-%m-%d %H:%M")
            messagebox.showinfo("Success", 
                              f"Download scheduled for {time_str}")
            
            self.dialog.destroy()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to schedule download: {e}")


class DownloadScheduler:
    """Main scheduler that manages scheduled downloads"""
    
    def __init__(self, download_manager):
        self.download_manager = download_manager
        self.scheduled_downloads: Dict[str, ScheduledDownload] = {}
        self.scheduler_file = Path("scheduled_downloads.json")
        self.running = False
        self.check_thread = None
        self.callbacks: List[Callable] = []
        
        self.load_scheduled_downloads()
        self.start_scheduler()
    
    def add_scheduled_download(self, scheduled_download: ScheduledDownload):
        """Add a new scheduled download"""
        self.scheduled_downloads[scheduled_download.schedule_id] = scheduled_download
        self.save_scheduled_downloads()
        self.notify_callbacks("added", scheduled_download.schedule_id)
    
    def remove_scheduled_download(self, schedule_id: str) -> bool:
        """Remove a scheduled download"""
        if schedule_id in self.scheduled_downloads:
            del self.scheduled_downloads[schedule_id]
            self.save_scheduled_downloads()
            self.notify_callbacks("removed", schedule_id)
            return True
        return False
    
    def get_scheduled_downloads(self) -> List[ScheduledDownload]:
        """Get all scheduled downloads"""
        return list(self.scheduled_downloads.values())
    
    def start_scheduler(self):
        """Start the background scheduler"""
        if self.running:
            return
        
        self.running = True
        self.check_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.check_thread.start()
    
    def stop_scheduler(self):
        """Stop the background scheduler"""
        self.running = False
        if self.check_thread:
            self.check_thread.join(timeout=5)
    
    def _scheduler_loop(self):
        """Background loop to check for scheduled downloads"""
        while self.running:
            try:
                now = datetime.now()
                ready_downloads = []
                
                # Check for downloads that are ready to start
                for schedule_id, scheduled_download in self.scheduled_downloads.items():
                    if (scheduled_download.status == "Scheduled" and 
                        scheduled_download.scheduled_time <= now):
                        ready_downloads.append(schedule_id)
                
                # Start ready downloads
                for schedule_id in ready_downloads:
                    self._start_scheduled_download(schedule_id)
                
                # Sleep for a while before next check
                time.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.warning("Scheduler loop failed: %s", redact_sensitive_text(str(e)))
                time.sleep(60)
    
    def _start_scheduled_download(self, schedule_id: str):
        """Start a scheduled download"""
        scheduled_download = self.scheduled_downloads.get(schedule_id)
        if not scheduled_download:
            return
        
        try:
            # Add to download manager
            download_id = self.download_manager.add_download(
                scheduled_download.url,
                scheduled_download.download_path,
                **scheduled_download.download_options
            )
            
            # Update scheduled download record
            scheduled_download.status = "Active"
            scheduled_download.download_id = download_id
            self.save_scheduled_downloads()
            self.notify_callbacks("started", schedule_id)
            
        except Exception as e:
            logger.warning("Failed to start scheduled download %s: %s", schedule_id, redact_sensitive_text(str(e)))
            scheduled_download.status = "Failed"
            self.save_scheduled_downloads()
            self.notify_callbacks("failed", schedule_id)
    
    def load_scheduled_downloads(self):
        """Load scheduled downloads from file"""
        if self.scheduler_file.exists():
            try:
                with open(self.scheduler_file, 'r') as f:
                    data = json.load(f)
                    for item in data:
                        scheduled_download = ScheduledDownload.from_dict(item)
                        # Only keep scheduled downloads (not completed ones)
                        if scheduled_download.status == "Scheduled":
                            self.scheduled_downloads[scheduled_download.schedule_id] = scheduled_download
            except Exception as e:
                logger.warning("Could not load scheduled downloads: %s", redact_sensitive_text(str(e)))
    
    def save_scheduled_downloads(self):
        """Save scheduled downloads to file"""
        try:
            data = [sd.to_dict() for sd in self.scheduled_downloads.values()]
            with open(self.scheduler_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error("Could not save scheduled downloads: %s", redact_sensitive_text(str(e)))
    
    def register_callback(self, callback: Callable):
        """Register callback for scheduler events"""
        self.callbacks.append(callback)
    
    def notify_callbacks(self, event: str, schedule_id: str):
        """Notify all registered callbacks"""
        for callback in self.callbacks:
            try:
                callback(event, schedule_id)
            except Exception as e:
                logger.exception("Scheduler callback failed")

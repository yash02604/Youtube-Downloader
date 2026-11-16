"""
Batch URL processing system for importing and managing multiple downloads
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import csv
import json
import logging
import threading
from typing import List, Dict, Optional
from urllib.parse import urlparse
from logging_utils import redact_sensitive_text

logger = logging.getLogger(__name__)


class BatchImportDialog:
    """Dialog for importing multiple URLs from various sources"""
    
    def __init__(self, parent, download_manager):
        self.parent = parent
        self.download_manager = download_manager
        self.urls = []
        
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Batch Import URLs")
        self.dialog.geometry("600x500")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the batch import dialog UI"""
        # Main frame
        main_frame = ttk.Frame(self.dialog, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Instructions
        ttk.Label(main_frame, text="Import multiple URLs for batch downloading", 
                 font=('Arial', 10, 'bold')).pack(anchor=tk.W, pady=(0, 10))
        
        # Import options notebook
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # Manual entry tab
        manual_frame = ttk.Frame(notebook, padding="10")
        notebook.add(manual_frame, text="Manual Entry")
        
        ttk.Label(manual_frame, text="Enter URLs (one per line):").pack(anchor=tk.W)
        self.text_area = tk.Text(manual_frame, height=15, width=70)
        self.text_area.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        
        # File import tab
        file_frame = ttk.Frame(notebook, padding="10")
        notebook.add(file_frame, text="From File")
        
        ttk.Label(file_frame, text="Import URLs from file (TXT, CSV, JSON):").pack(anchor=tk.W)
        
        file_button_frame = ttk.Frame(file_frame)
        file_button_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(file_button_frame, text="Browse File", 
                  command=self.browse_file).pack(side=tk.LEFT)
        
        self.file_label = ttk.Label(file_button_frame, text="No file selected")
        self.file_label.pack(side=tk.LEFT, padx=(10, 0))
        
        self.file_content = tk.Text(file_frame, height=12, width=70)
        self.file_content.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X)
        
        ttk.Button(button_frame, text="Import", 
                  command=self.import_urls).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(button_frame, text="Cancel", 
                  command=self.dialog.destroy).pack(side=tk.RIGHT)
        
        ttk.Button(button_frame, text="Preview", 
                  command=self.preview_urls).pack(side=tk.LEFT)
    
    def browse_file(self):
        """Browse for file to import URLs from"""
        file_path = filedialog.askopenfilename(
            title="Select URL file",
            filetypes=[
                ("Text files", "*.txt"),
                ("CSV files", "*.csv"),
                ("JSON files", "*.json"),
                ("All files", "*.*")
            ]
        )
        
        if file_path:
            self.file_label.config(text=f"Selected: {file_path}")
            self.load_file_content(file_path)
    
    def load_file_content(self, file_path: str):
        """Load and display file content"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                self.file_content.delete(1.0, tk.END)
                self.file_content.insert(1.0, content)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read file: {e}")
    
    def parse_urls_from_text(self, text: str) -> List[str]:
        """Extract URLs from text content"""
        urls = []
        lines = text.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if line and self.is_valid_url(line):
                urls.append(line)
        
        return urls
    
    def parse_csv_file(self, content: str) -> List[str]:
        """Parse URLs from CSV content"""
        urls = []
        try:
            reader = csv.reader(content.splitlines())
            for row in reader:
                for cell in row:
                    cell = cell.strip()
                    if cell and self.is_valid_url(cell):
                        urls.append(cell)
        except Exception as e:
            logger.warning("CSV URL parsing failed: %s", redact_sensitive_text(str(e)))
        
        return urls
    
    def parse_json_file(self, content: str) -> List[str]:
        """Parse URLs from JSON content"""
        urls = []
        try:
            data = json.loads(content)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, str) and self.is_valid_url(item):
                        urls.append(item)
                    elif isinstance(item, dict):
                        # Look for URL fields in dictionary
                        for key, value in item.items():
                            if 'url' in key.lower() and isinstance(value, str) and self.is_valid_url(value):
                                urls.append(value)
            elif isinstance(data, dict):
                # Look for URL fields in dictionary
                for key, value in data.items():
                    if 'url' in key.lower() and isinstance(value, str) and self.is_valid_url(value):
                        urls.append(value)
        except Exception as e:
            logger.warning("JSON URL parsing failed: %s", redact_sensitive_text(str(e)))
        
        return urls
    
    def is_valid_url(self, url: str) -> bool:
        """Validate if string is a proper URL"""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except (TypeError, ValueError):
            return False
    
    def preview_urls(self):
        """Show preview of detected URLs"""
        urls = self.get_all_urls()
        if not urls:
            messagebox.showinfo("Preview", "No valid URLs found")
            return
        
        preview_window = tk.Toplevel(self.dialog)
        preview_window.title("URL Preview")
        preview_window.geometry("500x400")
        
        ttk.Label(preview_window, text=f"Found {len(urls)} URLs:", 
                 font=('Arial', 10, 'bold')).pack(anchor=tk.W, padx=10, pady=5)
        
        listbox = tk.Listbox(preview_window)
        listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        for url in urls:
            listbox.insert(tk.END, url)
    
    def get_all_urls(self) -> List[str]:
        """Get all URLs from both manual entry and file"""
        urls = []
        
        # Get URLs from manual entry
        manual_text = self.text_area.get(1.0, tk.END).strip()
        if manual_text:
            urls.extend(self.parse_urls_from_text(manual_text))
        
        # Get URLs from file content
        file_text = self.file_content.get(1.0, tk.END).strip()
        if file_text:
            # Try to determine file type from content
            if file_text.strip().startswith('{') or file_text.strip().startswith('['):
                urls.extend(self.parse_json_file(file_text))
            elif ',' in file_text.split('\n')[0]:  # Likely CSV
                urls.extend(self.parse_csv_file(file_text))
            else:  # Plain text
                urls.extend(self.parse_urls_from_text(file_text))
        
        # Remove duplicates while preserving order
        seen = set()
        unique_urls = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)
        
        return unique_urls
    
    def import_urls(self):
        """Import and add all URLs to download manager"""
        urls = self.get_all_urls()
        
        if not urls:
            messagebox.showwarning("Warning", "No valid URLs found to import")
            return
        
        # Confirm import
        if not messagebox.askyesno("Confirm Import", 
                                  f"Import {len(urls)} URLs for downloading?"):
            return
        
        # Add URLs to download manager
        def add_downloads():
            for i, url in enumerate(urls):
                try:
                    self.download_manager.add_download(url, self.download_manager.default_path)
                    # Small delay to prevent overwhelming the system
                    if i < len(urls) - 1:
                        threading.Event().wait(0.1)
                except Exception as e:
                    logger.warning(
                        "Failed to queue URL %s: %s",
                        redact_sensitive_text(url), redact_sensitive_text(str(e)),
                    )
            
            messagebox.showinfo("Success", f"Added {len(urls)} downloads to queue")
        
        threading.Thread(target=add_downloads, daemon=True).start()
        self.dialog.destroy()


class BatchProcessor:
    """Manages batch operations on downloads"""
    
    def __init__(self, download_manager):
        self.download_manager = download_manager
    
    def pause_all(self):
        """Pause all active downloads"""
        active_downloads = [
            download_id for download_id, download in self.download_manager.downloads.items()
            if download.status == "Downloading"
        ]
        
        for download_id in active_downloads:
            self.download_manager.pause_download(download_id)
    
    def resume_all(self):
        """Resume all paused downloads"""
        paused_downloads = [
            download_id for download_id, download in self.download_manager.downloads.items()
            if download.status == "Paused"
        ]
        
        for download_id in paused_downloads:
            self.download_manager.resume_download(download_id)
    
    def cancel_all(self):
        """Cancel all active downloads"""
        if not messagebox.askyesno("Confirm", "Cancel all active downloads?"):
            return
        
        active_downloads = [
            download_id for download_id, download in self.download_manager.downloads.items()
            if download.status in ["Downloading", "Queued", "Analyzing"]
        ]
        
        for download_id in active_downloads:
            self.download_manager.cancel_download(download_id)
    
    def remove_completed(self):
        """Remove all completed downloads from history"""
        completed_downloads = [
            download_id for download_id, download in self.download_manager.downloads.items()
            if download.status in ["Completed", "Error", "Cancelled"]
        ]
        
        for download_id in completed_downloads:
            self.download_manager.remove_download(download_id)
        
        self.download_manager.clear_history_entries()

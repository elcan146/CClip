#!/usr/bin/env python3
"""YouTube analytics fetch thread for CClip."""

from datetime import datetime, timedelta

from PyQt6.QtCore import QThread, pyqtSignal


class AnalyticsFetchThread(QThread):
    """Thread to fetch YouTube analytics data in background.
    NOTE: This thread is tightly coupled to ClipMasterApp methods.
    It should be kept in ui/main_window.py or refactored to reduce coupling.
    """
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(dict, int, int)
    error = pyqtSignal(str)

    def __init__(self, parent_app):
        super().__init__()
        self.parent_app = parent_app

    def run(self):
        try:
            from googleapiclient.discovery import build as yt_build
            
            self.progress.emit("Authenticating...")
            creds = self.parent_app._get_youtube_credentials()
            if not creds:
                self.error.emit("Not authenticated. Please log in via the YouTube Panel first.")
                return
            
            self.progress.emit("Building API clients...")
            youtube = yt_build("youtube", "v3", credentials=creds)
            analytics = yt_build("youtubeAnalytics", "v2", credentials=creds)
            
            # Determine date range
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date_7d = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            start_date_28d = (datetime.now() - timedelta(days=28)).strftime("%Y-%m-%d")
            start_date_90d = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            start_date_lifetime = (datetime.now().replace(year=datetime.now().year - 5)).strftime("%Y-%m-%d")
            
            self.parent_app._deduct_quota("channels.list", 1)
            self.progress.emit("Fetching channel stats...")
            channel_stats = self.parent_app._fetch_channel_stats(youtube, analytics)
            if not channel_stats:
                self.error.emit("Failed to fetch channel statistics.")
                return
            
            self.progress.emit("Fetching video list...")
            videos = self.parent_app._fetch_video_list(youtube, channel_stats["channel_id"], max_results=50)
            self.parent_app._deduct_quota("videos.list", len(videos) // 50 + 1)
            self.parent_app._deduct_quota("playlistItems.list", 1)
            
            # Separate Shorts and regular videos
            shorts_video_ids = [v["id"] for v in videos if self.parent_app._is_short_video(v.get("duration", ""))]
            all_video_ids = [v["id"] for v in videos]
            
            self.progress.emit("Fetching cumulative growth data...")
            # Cumulative data (last 90 days for trends)
            cumulative_views_data = self.parent_app._fetch_analytics_time_series(
                analytics, start_date_90d, end_date, "views", "day"
            )
            cumulative_subs_data = self.parent_app._fetch_analytics_time_series(
                analytics, start_date_90d, end_date, "subscribersGained", "day"
            )
            cumulative_views = self.parent_app._calculate_cumulative_growth(cumulative_views_data)
            cumulative_subs = self.parent_app._calculate_cumulative_growth(cumulative_subs_data)
            
            self.progress.emit("Fetching daily activity...")
            # Daily activity (last 28 days)
            daily_views = self.parent_app._fetch_analytics_time_series(
                analytics, start_date_28d, end_date, "views", "day"
            )
            daily_subs = self.parent_app._fetch_analytics_time_series(
                analytics, start_date_28d, end_date, "subscribersGained,subscribersLost", "day"
            )
            daily_watch = self.parent_app._fetch_analytics_time_series(
                analytics, start_date_28d, end_date, "estimatedMinutesWatched", "day"
            )
            
            self.progress.emit("Fetching demographics...")
            # Demographics
            demographics = self.parent_app._fetch_demographics_data(analytics, start_date_28d, end_date)
            device_data = self.parent_app._fetch_device_data(analytics, start_date_28d, end_date)
            
            self.progress.emit("Fetching Shorts metrics...")
            # Shorts-specific metrics
            traffic_sources = self.parent_app._fetch_shorts_traffic_sources(analytics, shorts_video_ids, 7)
            retention_data = self.parent_app._fetch_shorts_retention(analytics, shorts_video_ids, 7)
            top_shorts = self.parent_app._fetch_top_shorts_this_week(youtube, analytics)
            
            if videos:
                self.progress.emit("Fetching video analytics...")
                analytics_data = self.parent_app._fetch_analytics_data(analytics, all_video_ids)
                videos = self.parent_app._merge_video_data(videos, analytics_data)
                self.parent_app._deduct_quota("youtubeAnalytics.reports.query", 1)
            
            self.progress.emit("Categorizing content...")
            categorized = {"Funny": [], "Badass": [], "Dramatic": [], "Uncategorized": []}
            for video in videos:
                cat = video.get("category", "Uncategorized")
                categorized[cat].append(video)
            
            by_type = {
                "Funny": len(categorized["Funny"]),
                "Badass": len(categorized["Badass"]),
                "Dramatic": len(categorized["Dramatic"]),
                "Uncategorized": len(categorized["Uncategorized"])
            }
            by_type_views = {
                "Funny": sum(v["views"] for v in categorized["Funny"]),
                "Badass": sum(v["views"] for v in categorized["Badass"]),
                "Dramatic": sum(v["views"] for v in categorized["Dramatic"]),
                "Uncategorized": sum(v["views"] for v in categorized["Uncategorized"])
            }
            
            used_quota, total_quota = self.parent_app._get_quota_status()
            
            result = {
                "channel": channel_stats,
                "videos": videos,
                "categorized": categorized,
                "total_videos": len(videos),
                "by_type": by_type,
                "by_type_views": by_type_views,
                # New comprehensive data
                "cumulative_views": cumulative_views,
                "cumulative_subs": cumulative_subs,
                "daily_views": daily_views,
                "daily_subs": daily_subs,
                "daily_watch": daily_watch,
                "demographics": demographics,
                "device_data": device_data,
                "traffic_sources": traffic_sources,
                "retention": retention_data,
                "top_shorts": top_shorts
            }
            
            self.progress.emit(f"Complete! Quota used: {used_quota}/{total_quota}")
            self.finished_ok.emit(result, used_quota, total_quota)
            
        except Exception as e:
            import traceback
            error_msg = f"Analytics error: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            self.error.emit(str(e))

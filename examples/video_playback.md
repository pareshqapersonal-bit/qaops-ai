# Feature: Video Playback

## Description
Subscribers can stream videos from the catalogue on mobile and TV apps.

## Requirements
1. Tapping a title from the catalogue starts playback within the player
   screen.
2. The player offers play/pause, seek, subtitle selection, and video quality
   selection (Auto, 480p, 720p, 1080p).
3. Quality "Auto" adapts to available bandwidth.
4. Playback resumes from the last watched position when a subscriber reopens
   a partially watched title.
5. Subtitles, when enabled, stay in sync with the audio.
6. Content marked "Kids" is playable under a kids profile; other content is
   not visible in a kids profile.
7. A subscriber's plan limits the number of simultaneous streams.
8. Playback events are reported for analytics.

## Notes
- The player should handle poor network conditions gracefully.
- DRM-protected titles play only on supported devices.

import { Platform } from 'react-native';
import * as RNFS from '@dr.pogodin/react-native-fs';
import Share from 'react-native-share';
import Clipboard from '@react-native-clipboard/clipboard';
import { videoUrl } from '../api/client';

export interface VideoShareData {
  id: number;
  social_title?: string | null;
  topic?: string | null;
  social_description?: string | null;
  social_tags?: string[] | null;
  youtube_url?: string | null;
}

/**
 * Build the metadata text to copy to clipboard:
 * Title, description, and tags formatted for social media posts.
 */
function buildMetadataText(video: VideoShareData): string {
  const title = video.social_title || video.topic || `Vídeo #${video.id}`;
  const parts: string[] = [title];

  if (video.social_description) {
    parts.push('', video.social_description);
  }

  if (video.social_tags && video.social_tags.length > 0) {
    parts.push('', video.social_tags.map((t) => `#${t}`).join(' '));
  }

  return parts.join('\n');
}

/**
 * Get the local cached path for a video, if it exists.
 * Returns null if not downloaded.
 */
export async function getLocalVideoPath(videoId: number): Promise<string | null> {
  const filename = `gpcg_video_${videoId}.mp4`;
  const localPath = `${RNFS.CachesDirectoryPath}/shared/${filename}`;
  try {
    const exists = await RNFS.exists(localPath);
    return exists ? localPath : null;
  } catch {
    return null;
  }
}

/**
 * Download a video to the device cache with progress.
 * Returns the local file path on success.
 */
export async function downloadVideo(
  videoId: number,
  onProgress?: (pct: number) => void,
): Promise<string> {
  const remoteUrl = videoUrl(videoId);
  const filename = `gpcg_video_${videoId}.mp4`;
  const localPath = `${RNFS.CachesDirectoryPath}/shared/${filename}`;
  const dirPath = `${RNFS.CachesDirectoryPath}/shared`;

  // Ensure directory exists
  try {
    await RNFS.mkdir(dirPath);
  } catch (e) {
    // Directory may already exist
  }

  // Clean up any previous download of the same file
  try {
    await RNFS.unlink(localPath);
  } catch (e) {
    // File may not exist
  }

  // Download with progress
  const downloadTask = RNFS.downloadFile({
    fromUrl: remoteUrl,
    toFile: localPath,
    progress: (data: { bytesWritten: number; contentLength: number }) => {
      if (onProgress && data.contentLength > 0) {
        const pct = Math.round((data.bytesWritten / data.contentLength) * 100);
        onProgress(pct);
      }
    },
    progressDivider: 5,
  });

  const ret = await downloadTask.promise;

  if (ret.statusCode !== 200) {
    throw new Error(`Download falhou (HTTP ${ret.statusCode})`);
  }

  // Verify file exists
  const exists = await RNFS.exists(localPath);
  if (!exists) {
    throw new Error('Arquivo não foi salvo corretamente');
  }

  return localPath;
}

/**
 * Open the native share sheet with a video file.
 * Copies metadata to clipboard first, then shares ONLY the file.
 *
 * On Android, passing text alongside a video file causes some apps
 * (Instagram, TikTok) to receive the text instead of the video.
 * So we copy the text to clipboard and share only the file —
 * the user pastes the text manually after choosing the app.
 */
export async function shareVideoFile(
  video: VideoShareData,
  localPath: string,
): Promise<void> {
  const metadataText = buildMetadataText(video);

  // Copy metadata to clipboard — user pastes it manually in the target app
  Clipboard.setString(metadataText);

  // Build the file URI for sharing
  const shareUrl =
    Platform.OS === 'android' ? `file://${localPath}` : localPath;

  // Share ONLY the video file — no message field, otherwise Android
  // apps may pick up the text instead of the video
  await Share.open({
    title: video.social_title || video.topic || 'Vídeo',
    url: shareUrl,
    type: 'video/mp4',
  });
}

/**
 * Share a YouTube URL (for already-published videos).
 * Copies metadata to clipboard first, then shares only the URL.
 */
export async function shareYouTubeUrl(video: VideoShareData): Promise<void> {
  const metadataText = buildMetadataText(video);
  Clipboard.setString(metadataText);

  await Share.open({
    title: video.social_title || video.topic || 'Vídeo',
    url: video.youtube_url!,
  });
}

/**
 * Copy only the metadata (title + description + tags) to clipboard.
 */
export function copyVideoMetadata(video: VideoShareData): string {
  const text = buildMetadataText(video);
  Clipboard.setString(text);
  return text;
}

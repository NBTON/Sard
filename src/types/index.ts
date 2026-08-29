export interface Citation {
  citation_id: string;
  title: string;
  source_name: string;
  source_url: string;
  chunk_id?: string;
  snippet?: string;
  topic?: string;
}

export type ArtifactKind = "document" | "presentation" | "calendar" | "image" | "diagram" | "interactive";
export type ArtifactFormat = "pdf" | "docx" | "pptx" | "ics" | "svg" | "png" | "json" | string;
export type ArtifactStatus = "pending" | "created" | "failed" | "skipped";

export interface Artifact {
  id: string;
  kind: ArtifactKind;
  format: ArtifactFormat;
  title: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  status: ArtifactStatus;
  download_url: string | null;
  preview?: any;
  warnings?: string[];
  error?: string | null;
  checksum?: string | null;
  // Compatibility fields
  type?: string;
  url?: string;
  data?: any;
}

export interface Attachment {
  id: string;
  attachment_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  url?: string;
  preview_url?: string;
  uploading?: boolean;
  error?: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  citations?: Citation[];
  artifacts?: Artifact[];
  attachments?: Attachment[];
  isThinking?: boolean;
  statusStage?: string;
  isStreaming?: boolean;
  error?: string;
}

export interface ChatSession {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
}

export interface SystemStatus {
  status_label: string;
  verified: boolean;
  sources_count?: number;
  updated_at?: string;
  sources: { verified: boolean };
  moc_branding?: string;
}

export type Lang = "ar" | "en";
export type View = "landing" | "chat";

export interface Sector {
  id: string;
  ar: string;
  en: string;
  color: string;
  promptAr: string;
  promptEn: string;
}

export interface Citation {
  citation_id: string;
  title: string;
  source_name: string;
  source_url: string;
  chunk_id?: string;
  snippet?: string;
  topic?: string;
}

export interface Artifact {
  type: "pdf" | "pptx" | "ics" | "card" | "etiquette_flow" | "dialect_lore" | "artisan_craft" | "verified_research" | "family_memoir_booklet" | "presentation_pptx" | "recipe_craft_card" | "markdown" | string;
  title: string;
  url: string;
  filename: string;
  data?: any;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  citations?: Citation[];
  artifacts?: Artifact[];
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

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
  type: "pdf" | "ics" | "markdown" | string;
  title: string;
  url: string;
  filename: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  citations?: Citation[];
  artifacts?: Artifact[];
  statusText?: string;
  isStreaming?: boolean;
  timings?: {
    total_ms?: number;
    retrieve_ms?: number;
    generation_ms?: number;
  };
  modelUsed?: string;
  retrievalMode?: string;
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
  model: {
    provider: string;
    model_name: string;
    temperature?: number;
    error?: string;
  };
  rag: {
    available: boolean;
    collection_path?: string;
    embedding_model?: string;
    error?: string;
  };
  moc_branding: string;
}

export interface CulturalSuggestion {
  id: string;
  title: string;
  description: string;
  query: string;
  category: "heritage" | "culinary" | "arts" | "nature" | "itinerary";
  iconName: string;
}

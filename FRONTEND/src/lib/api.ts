// multipass-frontend/src/lib/api.ts
export const API_BASE_URL = 'http://localhost:8001';  // Yeni port numarasını yazın

interface VMOperation {
  type: 'creating' | 'success' | 'error';
  vmName?: string;
}

interface ExecutionResult {
  success: boolean;
  operation: string;
  details?: string;
}

export interface ChatResponse {
  response: string;
  vmOperation?: VMOperation;
  execution_results?: ExecutionResult[];
}

export const sendChatMessage = async (message: string, sessionId: string): Promise<ChatResponse> => {
  const response = await fetch(`${API_BASE_URL}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, sessionId })
  });
  
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }
  
  return await response.json();
};

export interface VMInfo {
  name: string;
  state: string;
  ipv4?: string[];
  release?: string;
  disk_usage?: string;
  memory_usage?: string;
}

export interface VMListResponse {
  success: boolean;
  vms: VMInfo[];
  error?: string;
}

export const getVMList = async (): Promise<VMListResponse> => {
  const response = await fetch(`${API_BASE_URL}/vms/list`);
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }
  const data = await response.json();
  return { success: true, vms: data.list || [] };
};

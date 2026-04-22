import axios from 'axios';

async function test_endpoints() {
  const API_BASE = 'http://localhost:3001/api';
  
  try {
    console.log('[Test] Fetching system health...');
    const health = await axios.get(`${API_BASE}/system/health`);
    console.log('[Test] Health status:', health.data);
    
    console.log('[Test] Fetching wallet summary...');
    const wallet = await axios.get(`${API_BASE}/wallet/summary`);
    console.log('[Test] Wallet summary:', wallet.data);
    
  } catch (err: any) {
    console.error('[Test] FAILED:', err.message);
  }
}

test_endpoints();

import axios from 'axios';

// PitchIQ API client
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  headers: {
    'Content-Type': 'application/json',
  },
});

export const pitchiqApi = {
  // Diagnostics
  checkHealth: async () => {
    const res = await api.get('/health');
    return res.data;
  },

  checkReady: async () => {
    const res = await api.get('/ready');
    return res.data;
  },

  // Data endpoints
  getTeams: async () => {
    const res = await api.get('/api/v1/teams');
    return res.data;
  },

  getTeamForm: async (team) => {
    const res = await api.get(`/api/v1/teams/${encodeURIComponent(team)}/form`);
    return res.data;
  },

  getTeamAvailability: async (team) => {
    const res = await api.get(`/api/v1/teams/${encodeURIComponent(team)}/availability`);
    return res.data;
  },

  getFixtures: async () => {
    const res = await api.get('/api/v1/fixtures');
    return res.data;
  },

  getModelCard: async () => {
    const res = await api.get('/api/v1/model');
    return res.data;
  },

  predictMatch: async ({ home_team, away_team, match_date }) => {
    const res = await api.post('/api/v1/predict', {
      home_team,
      away_team,
      ...(match_date ? { match_date } : {}),
    });
    return res.data;
  },

  // Conversational Analyst endpoint
  askChat: async ({ question, thread_id }) => {
    const res = await api.post('/api/v1/chat', {
      question,
      ...(thread_id ? { thread_id } : {}),
    });
    return res.data;
  },
};

export default api;

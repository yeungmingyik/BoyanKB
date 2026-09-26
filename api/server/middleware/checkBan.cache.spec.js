const mockCached = new Map();
const mockLogs = new Map();
const mockBanCache = {
  get: jest.fn(async (key) => mockCached.get(key)),
  set: jest.fn(async (key, value) => mockCached.set(key, value)),
};
const mockBanLogs = {
  opts: { ttl: 60_000 },
  get: jest.fn(async (key) => mockLogs.get(key)),
  delete: jest.fn(async (key) => mockLogs.delete(key)),
};

jest.mock('keyv', () => ({ Keyv: jest.fn(() => mockBanCache) }));
jest.mock('@librechat/data-schemas', () => ({ logger: { error: jest.fn(), warn: jest.fn() } }));
jest.mock('@librechat/api', () => ({
  isEnabled: (value) => value === 'true',
  keyvMongo: {},
  removePorts: (req) => req.ip,
}));
jest.mock('~/cache', () => ({ getLogStores: () => mockBanLogs }));
jest.mock('~/models', () => ({ findUser: jest.fn() }));
jest.mock('./denyRequest', () => jest.fn());
jest.mock('./oauthNavigation', () => ({ isOAuthNavigation: () => false }));

const checkBan = require('./checkBan');

function req(userId, ip = '192.0.2.50') {
  return { user: { id: userId }, ip, headers: {}, method: 'GET', originalUrl: '/api/convos' };
}

function res() {
  return { status: jest.fn().mockReturnThis(), json: jest.fn().mockReturnThis() };
}

describe('individual ban cache boundaries', () => {
  beforeEach(() => {
    mockCached.clear();
    mockLogs.clear();
    jest.clearAllMocks();
    process.env.BAN_VIOLATIONS = 'true';
    delete process.env.USE_REDIS;
  });

  it('does not ban another user sharing the banned user IP', async () => {
    mockLogs.set('user-a', { expiresAt: Date.now() + 60_000 });
    const bannedNext = jest.fn();
    await checkBan(req('user-a'), res(), bannedNext);
    expect(bannedNext).not.toHaveBeenCalled();
    expect(mockCached.has('user-a')).toBe(true);
    expect(mockCached.has('192.0.2.50')).toBe(false);
    const otherNext = jest.fn();
    await checkBan(req('user-b'), res(), otherNext);
    expect(otherNext).toHaveBeenCalledWith();
  });

  it('does not attach an IP ban to a user who moves to another IP', async () => {
    mockLogs.set('192.0.2.50', { expiresAt: Date.now() + 60_000 });
    const bannedNext = jest.fn();
    await checkBan(req('user-a'), res(), bannedNext);
    expect(bannedNext).not.toHaveBeenCalled();
    expect(mockCached.has('user-a')).toBe(false);
    const movedNext = jest.fn();
    await checkBan(req('user-a', '192.0.2.51'), res(), movedNext);
    expect(movedNext).toHaveBeenCalledWith();
  });

  it('still blocks other users on an explicitly banned IP', async () => {
    mockLogs.set('192.0.2.50', { expiresAt: Date.now() + 60_000 });
    await checkBan(req('user-a'), res(), jest.fn());
    const next = jest.fn();
    await checkBan(req('user-b'), res(), next);
    expect(next).not.toHaveBeenCalled();
  });

  it('preserves an active user ban when the IP ban has expired', async () => {
    mockLogs.set('192.0.2.50', { expiresAt: Date.now() - 1 });
    mockLogs.set('user-a', { expiresAt: Date.now() + 60_000 });
    const next = jest.fn();
    await checkBan(req('user-a'), res(), next);
    expect(next).not.toHaveBeenCalled();
    expect(mockLogs.has('192.0.2.50')).toBe(false);
    expect(mockLogs.has('user-a')).toBe(true);
  });
});

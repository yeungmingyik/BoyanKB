const mongoose = require('mongoose');

const mockFindOne = jest.fn();
const mockBanViolation = jest.fn().mockResolvedValue(undefined);
const mockSilentExit = jest.fn();

jest.mock('../connect', () => jest.fn().mockResolvedValue(undefined));
jest.mock('@librechat/data-schemas', () => ({
  createModels: () => ({ User: { findOne: mockFindOne } }),
}));
jest.mock('~/cache/banViolation', () => mockBanViolation);
jest.mock('../helpers', () => ({
  ...jest.requireActual('../helpers'),
  silentExit: mockSilentExit,
}));

const USER_ID = '67c000000000000000000001';

describe('Ban user CLI', () => {
  const originalArgv = process.argv;
  let existingHandlers;

  beforeEach(() => {
    process.argv = ['node', 'ban-user.js', 'partner@example.invalid', '60000'];
    existingHandlers = new Set(process.listeners('uncaughtException'));
    jest.spyOn(console, 'log').mockImplementation(() => undefined);
  });

  afterEach(() => {
    process.argv = originalArgv;
    process
      .listeners('uncaughtException')
      .filter((handler) => !existingHandlers.has(handler))
      .forEach((handler) => process.removeListener('uncaughtException', handler));
    jest.restoreAllMocks();
  });

  it.each([
    ['MongoDB ObjectId', new mongoose.Types.ObjectId(USER_ID)],
    ['string', USER_ID],
  ])('passes a string cache key for a %s user ID', async (_name, userId) => {
    mockFindOne.mockReturnValue({
      lean: jest.fn().mockResolvedValue({ _id: userId, email: 'partner@example.invalid' }),
    });
    const completion = new Promise((resolve) => mockSilentExit.mockImplementation(resolve));

    jest.isolateModules(() => require('../ban-user'));

    expect(await completion).toBe(0);
    expect(mockFindOne).toHaveBeenCalledWith({ email: 'partner@example.invalid' });
    expect(mockBanViolation).toHaveBeenCalledWith(
      {},
      expect.any(Object),
      expect.objectContaining({ user_id: USER_ID, duration: 60000, violation_count: 20 }),
    );
  });
});

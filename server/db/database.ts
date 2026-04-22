import { query, initPostgres } from './postgres';

/**
 * Initialize Tables
 */
export const init_db = async () => {
  try {
    await initPostgres();
    console.log(`[Database] PostgreSQL initialized`);
  } catch (error) {
    console.error(`[Database] Error initializing PostgreSQL:`, error);
    throw error;
  }
};

export { query };
export default query;

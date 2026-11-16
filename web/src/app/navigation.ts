import { matchPath } from 'react-router-dom';

export const navigation = [
  ['/', 'Dashboard'], ['/transactions', 'Transactions'], ['/assets', 'Assets'],
  ['/mileage', 'Mileage'], ['/accounts', 'Accounts'], ['/categories', 'Categories & contacts'],
  ['/reports', 'Reports'], ['/data', 'Data & backup'],
] as const;

export const entryRoutes = [
  { path: '/transactions', label: 'Transactions' }, { path: '/journals', label: 'Journals' },
  { path: '/mixed-use', label: 'Mixed-use expenses' }, { path: '/recurring', label: 'Recurring expenses' },
] as const;
export const mileageRoutes = [
  { path: '/mileage', label: 'View mileage log' }, { path: '/mileage/vehicles', label: 'Vehicle management' }, { path: '/mileage/new', label: 'Add trip', write: true },
] as const;
export const referenceRoutes = [
  { path: '/categories', label: 'Categories' }, { path: '/payees', label: 'Payees' }, { path: '/tags', label: 'Tags' },
] as const;
export const matchesRoute = (pathname: string, path: string) => matchPath({ path, end: path === '/' }, pathname) !== null;
export const isEntryRoute = (pathname: string) => entryRoutes.some(route => matchesRoute(pathname, route.path));
export const isReferenceRoute = (pathname: string) => referenceRoutes.some(route => matchesRoute(pathname, route.path));

export const SUPPORT_EMAIL = 'heathcoteadvisoryllc@gmail.com';
export const categories = ['Bookkeeping', 'Export', 'Receipts', 'Access', 'Privacy request', 'Routine defect', 'Product feedback', 'Security or data loss'] as const;
export type HelpCategory = typeof categories[number];
export const severityFor = (category: HelpCategory) => category === 'Security or data loss' ? 'P0'
  : ['Bookkeeping', 'Export', 'Receipts', 'Access'].includes(category) ? 'P1' : category === 'Product feedback' ? 'P3' : 'P2';

export function helpDraft(category: HelpCategory, reference: string, release: string, research: boolean, answers: string) {
  // Only explicit user-entered, reviewed fields. No session/business/page context.
  const severity = severityFor(category);
  return `Category: ${category}\nProvisional priority: ${severity}\nRequest/reproduction reference: ${reference || 'Not available'}\nRelease: ${release || 'Not available'}\n` +
    'Describe the task, expected result, actual result and safe steps in your mail client. Do not attach financial records, receipts, screenshots, business identifiers, credentials or tokens.\n' +
    (research ? `\nOptional qualitative research: I choose to share these answers for product learning, without marketing enrollment.\n${answers}\n` : '\nOptional product research: not opted in.\n');
}

import type { Schemas } from '../../api/client';

export type MileageDraft = Schemas['MileageChanges'] & { round_trip?: boolean; return_miles?: string | null; maps_reviewed?: boolean };

export function mileagePayload(values: MileageDraft) {
  const { maps_reviewed, vehicle, ...payload } = values;
  void vehicle;
  void maps_reviewed;
  return { ...payload, vehicle: '' };
}

export function mileageLoaded(row: Schemas['Mileage']) {
  const values: MileageDraft = { trip_date: row.trip_date, miles: row.miles, vehicle: row.vehicle, vehicle_id: row.vehicle_id ?? '',
    start_location: row.start_location, destination: row.destination, business_purpose: row.business_purpose,
    notes: row.notes, tag_ids: row.tag_ids, new_tag_name: '', maps_reviewed: false };
  return { values, metadata: { id: row.id, baseline: row.baseline } };
}

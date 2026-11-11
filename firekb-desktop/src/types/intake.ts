export interface IntakeCourse { id: string; name: string; aliases?: string[]; [key: string]: unknown }
export interface Slot { course_id: string; weekday: number; start: string; end: string; weeks: string | number[] }
export interface Exception { course_id: string; date: string; start: string; end: string; action: 'add' | 'cancel' }
export interface Timetable { version: number; semester_start: string; total_weeks: number; timezone: string; tolerance_minutes: number; slots: Slot[]; exceptions: Exception[] }
export interface TimetableState { timetable: Timetable; courses: IntakeCourse[] }
export interface IntakeFile { name: string; path: string; size: number; course_id: string | null; status: string; reason: string; kind: string; date?: string; selected?: boolean; done?: boolean }
export interface ImportResult { source: string; status: string; path?: string; error?: string }
export type IntakeAction = 'state' | 'save' | 'parse' | 'preview' | 'import'

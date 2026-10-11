import React, { useState } from 'react';
import { QuestionnaireAnswers } from '@/types/contracts';
import { Card, CardHeader, CardTitle, CardContent, CardFooter } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Checkbox } from '@/components/ui/checkbox';

interface QuestionnaireProps {
    onComplete: (answers: QuestionnaireAnswers) => void;
}

export const QuestionnaireForm: React.FC<QuestionnaireProps> = ({ onComplete }) => {
    const [section, setSection] = useState<number>(1);
    const [medications, setMedications] = useState([{ name: '', dose: '', unit: 'mg', times_per_day: 1, since: '' }]);
    const [meals, setMeals] = useState([{ name: '', details: '' }]);
    const [answers, setAnswers] = useState<Partial<QuestionnaireAnswers>>({
        necessary_processing_consent: false,
        optional_personal_history_consent: false,
        hba1c_unit: '%',
        fasting_glucose_unit: 'mg/dL',
        spike_threshold_unit: 'mg/dL',
        spike_threshold_value: 180,
    });

    const update = <K extends keyof QuestionnaireAnswers>(key: K, val: QuestionnaireAnswers[K]) => {
        setAnswers((prev) => ({ ...prev, [key]: val }));
    };

    const nextSection = () => {
        // Conditional Branching Skip Rules
        if (section === 2 && answers.diagnosis_status === 'none') {
            setSection(3); // Continue to monitoring
        } else {
            setSection((s) => Math.min(s + 1, 8));
        }
    };

    const prevSection = () => setSection((s) => Math.max(s - 1, 1));

    const handleSubmit = () => {
        if (!answers.necessary_processing_consent) {
            alert("Explicit consent for necessary glucose and meal processing is required.");
            return;
        }
        onComplete({ ...answers, prescribed_medications: medications.filter((medication) => medication.name.trim()), meals: meals.filter((meal) => meal.name.trim()) } as QuestionnaireAnswers);
    };

    return (
        <Card className="max-w-2xl mx-auto my-6 shadow-md">
            <CardHeader>
                <div className="flex justify-between items-center text-xs text-muted-foreground mb-1">
                    <span>Section {section} of 8</span>
                    <span>{Math.round((section / 8) * 100)}% Completed</span>
                </div>
                <CardTitle className="text-xl">
                    {section === 1 && "1. Basic Profile"}
                    {section === 2 && "2. Metabolic Health"}
                    {section === 3 && "3. Glucose Monitoring Devices"}
                    {section === 4 && "4. Medications"}
                    {section === 5 && "5. Meal Habits"}
                    {section === 6 && "6. Wearables"}
                    {section === 7 && "7. Spike Forecast Alerts"}
                    {section === 8 && "8. Consent & Data Usage"}
                </CardTitle>
            </CardHeader>

            <CardContent className="space-y-4">
                {/* SECTION 1: BASICS */}
                {section === 1 && (
                    <>
                        <div>
                            <Label>Date of Birth</Label>
                            <Input type="date" value={answers.dob || ''} onChange={(e) => update('dob', e.target.value)} />
                        </div>
                        <div>
                            <Label>Sex Assigned at Birth</Label>
                            <Select value={answers.sex_assigned_at_birth ?? ''} onValueChange={(v: any) => update('sex_assigned_at_birth', v)}>
                                <SelectTrigger><SelectValue placeholder="Select" /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="male">Male</SelectItem>
                                    <SelectItem value="female">Female</SelectItem>
                                    <SelectItem value="intersex">Intersex</SelectItem>
                                    <SelectItem value="prefer_not_to_say">Prefer not to say</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <Label>Height (cm)</Label>
                                <Input type="number" value={answers.height_cm || ''} onChange={(e) => update('height_cm', Number(e.target.value))} />
                            </div>
                            <div>
                                <Label>Weight (kg)</Label>
                                <Input type="number" value={answers.weight_kg || ''} onChange={(e) => update('weight_kg', Number(e.target.value))} />
                            </div>
                        </div>
                    </>
                )}

                {/* SECTION 2: METABOLIC */}
                {section === 2 && (
                    <>
                        <div>
                            <Label>Diagnosis Status</Label>
                            <Select value={answers.diagnosis_status ?? ''} onValueChange={(v: any) => update('diagnosis_status', v)}>
                                <SelectTrigger><SelectValue placeholder="Select" /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="none">None / No Diagnosis</SelectItem>
                                    <SelectItem value="prediabetes">Prediabetes</SelectItem>
                                    <SelectItem value="type_2">Type 2 Diabetes</SelectItem>
                                    <SelectItem value="type_1">Type 1 Diabetes</SelectItem>
                                    <SelectItem value="gestational">Gestational Diabetes</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        {answers.diagnosis_status && answers.diagnosis_status !== 'none' && (
                            <div>
                                <Label>Diagnosis Year</Label>
                                <Input type="number" placeholder="e.g. 2020" value={answers.diagnosis_year || ''} onChange={(e) => update('diagnosis_year', Number(e.target.value))} />
                            </div>
                        )}
                    </>
                )}

                {/* SECTION 4: MEDICATIONS */}
                {section === 4 && (
                    <div className="space-y-4">
                        <p className="text-sm text-muted-foreground">Add each glucose-affecting medicine you currently take. This is for organizing your profile and is not a dosing recommendation.</p>
                        {medications.map((medication, index) => (
                            <div key={index} className="space-y-3 rounded-lg border p-4">
                                <div className="grid gap-3 sm:grid-cols-2">
                                    <div><Label>Medicine</Label><Input placeholder="e.g. Metformin" value={medication.name} onChange={(e) => setMedications((items) => items.map((item, i) => i === index ? { ...item, name: e.target.value } : item))} /></div>
                                    <div><Label>Amount</Label><Input type="number" min="0" placeholder="500" value={medication.dose} onChange={(e) => setMedications((items) => items.map((item, i) => i === index ? { ...item, dose: e.target.value } : item))} /></div>
                                    <div><Label>Unit</Label><Select value={medication.unit} onValueChange={(value) => setMedications((items) => items.map((item, i) => i === index ? { ...item, unit: value } : item))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="mg">mg</SelectItem><SelectItem value="units">units</SelectItem><SelectItem value="mcg">mcg</SelectItem><SelectItem value="tablet">tablet</SelectItem></SelectContent></Select></div>
                                    <div><Label>Times per day</Label><Input type="number" min="1" max="12" value={medication.times_per_day} onChange={(e) => setMedications((items) => items.map((item, i) => i === index ? { ...item, times_per_day: Number(e.target.value) } : item))} /></div>
                                </div>
                                <div><Label>Since when?</Label><Input type="date" value={medication.since} onChange={(e) => setMedications((items) => items.map((item, i) => i === index ? { ...item, since: e.target.value } : item))} /></div>
                                {medications.length > 1 && <Button type="button" variant="outline" onClick={() => setMedications((items) => items.filter((_, i) => i !== index))}>Remove medicine</Button>}
                            </div>
                        ))}
                        <Button type="button" variant="outline" onClick={() => setMedications((items) => [...items, { name: '', dose: '', unit: 'mg', times_per_day: 1, since: '' }])}>Add medicine</Button>
                    </div>
                )}

                {/* SECTION 5: MEALS */}
                {section === 5 && (
                    <div className="space-y-4">
                        <div><Label>Meal habits and dietary pattern</Label><Input placeholder="e.g. Mostly home-cooked, lower-carb" value={answers.dietary_pattern ?? ''} onChange={(e) => update('dietary_pattern', e.target.value)} /></div>
                        <p className="text-sm text-muted-foreground">Add meals you commonly eat. You can add more later.</p>
                        {meals.map((meal, index) => (
                            <div key={index} className="flex gap-2">
                                <Input placeholder="Meal name" value={meal.name} onChange={(e) => setMeals((items) => items.map((item, i) => i === index ? { ...item, name: e.target.value } : item))} />
                                <Input placeholder="Optional notes" value={meal.details} onChange={(e) => setMeals((items) => items.map((item, i) => i === index ? { ...item, details: e.target.value } : item))} />
                                {meals.length > 1 && <Button type="button" variant="outline" onClick={() => setMeals((items) => items.filter((_, i) => i !== index))}>Remove</Button>}
                            </div>
                        ))}
                        <Button type="button" variant="outline" onClick={() => setMeals((items) => [...items, { name: '', details: '' }])}>Add new meal</Button>
                    </div>
                )}

                {/* SECTION 6: WEARABLES */}
                {section === 6 && (
                    <div className="space-y-4">
                        <div><Label>Wearable brand</Label><Select value={answers.wearable_brand ?? ''} onValueChange={(value) => update('wearable_brand', value)}><SelectTrigger><SelectValue placeholder="Select a wearable" /></SelectTrigger><SelectContent>
                            {['Apple Watch', 'Fitbit', 'Garmin', 'Oura Ring', 'Samsung Galaxy Watch', 'WHOOP', 'Withings ScanWatch', 'Amazfit'].map((brand) => <SelectItem key={brand} value={brand}>{brand}</SelectItem>)}
                            <SelectItem value="other">Other</SelectItem>
                        </SelectContent></Select></div>
                        {answers.wearable_brand && answers.wearable_brand !== 'other' && <div><Label>Version or model</Label><Select value={answers.wearable_version ?? ''} onValueChange={(value) => update('wearable_version', value)}><SelectTrigger><SelectValue placeholder="Select a version" /></SelectTrigger><SelectContent>
                            {(answers.wearable_brand === 'Apple Watch' ? ['Series 10', 'Series 9', 'Series 8', 'Ultra 2', 'SE (2nd generation)'] : answers.wearable_brand === 'Fitbit' ? ['Charge 6', 'Versa 4', 'Sense 2', 'Inspire 3'] : answers.wearable_brand === 'Garmin' ? ['Venu 3', 'Forerunner 265', 'vívoactive 5', 'Lily 2'] : answers.wearable_brand === 'Oura Ring' ? ['Gen 3', 'Oura Ring 4'] : ['Current model', 'Previous model']).map((version) => <SelectItem key={version} value={version}>{version}</SelectItem>)}
                        </SelectContent></Select></div>}
                        {answers.wearable_brand === 'other' && <div><Label>Other wearable</Label><Input placeholder="Brand and version" value={answers.wearable_other ?? ''} onChange={(e) => update('wearable_other', e.target.value)} /></div>}
                    </div>
                )}

                {/* SECTION 7: ALERTS */}
                {section === 7 && (
                    <>
                        <div className="flex items-center space-x-2">
                            <Checkbox id="spike_alerts" checked={Boolean(answers.enable_spike_alerts)} onCheckedChange={(chk) => update('enable_spike_alerts', Boolean(chk))} />
                            <Label htmlFor="spike_alerts">Enable non-clinical predicted spike notifications</Label>
                        </div>
                        {answers.enable_spike_alerts && (
                            <div className="grid grid-cols-2 gap-4 pt-2">
                                <div>
                                    <Label>Alert Threshold ({answers.spike_threshold_unit})</Label>
                                    <Input type="number" value={answers.spike_threshold_value || 180} onChange={(e) => update('spike_threshold_value', Number(e.target.value))} />
                                </div>
                                <div>
                                    <Label>Unit</Label>
                                    <Select value={answers.spike_threshold_unit ?? 'mg/dL'} onValueChange={(v: any) => update('spike_threshold_unit', v)}>
                                        <SelectTrigger><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="mg/dL">mg/dL</SelectItem>
                                            <SelectItem value="mmol/L">mmol/L</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>
                        )}
                    </>
                )}

                {/* SECTION 8: CONSENT */}
                {section === 8 && (
                    <div className="space-y-4">
                        <div className="p-3 bg-red-50 border-l-4 border-red-500 text-xs text-red-700 rounded">
                            <p className="font-bold">Required Processing Consent</p>
                            <p>GlucoTwin requires access to process meal images and glucose data to function.</p>
                        </div>
                        <div className="flex items-center space-x-2">
                            <Checkbox id="necessary_consent" checked={Boolean(answers.necessary_processing_consent)} onCheckedChange={(chk) => update('necessary_processing_consent', Boolean(chk))} />
                            <Label htmlFor="necessary_consent" className="text-sm font-semibold">I consent to necessary meal photo and glucose processing *</Label>
                        </div>
                        <div className="flex items-center space-x-2 pt-2">
                            <Checkbox id="optional_consent" checked={Boolean(answers.optional_personal_history_consent)} onCheckedChange={(chk) => update('optional_personal_history_consent', Boolean(chk))} />
                            <Label htmlFor="optional_consent" className="text-sm">Optional: I consent to using completed meal responses for future model personalization</Label>
                        </div>
                    </div>
                )}
            </CardContent>

            <CardFooter className="flex justify-between">
                <Button variant="outline" onClick={prevSection} disabled={section === 1}>Back</Button>
                {section < 8 ? (
                    <Button onClick={nextSection}>Save & Continue</Button>
                ) : (
                    <Button onClick={handleSubmit} disabled={!answers.necessary_processing_consent}>Complete Onboarding</Button>
                )}
            </CardFooter>
        </Card>
    );
};
{ Altium Library Manager - import script (DelphiScript, Altium Designer AD26).
  Generated per job by alm/altium/bridge.py: {{JOB_FILE}} is replaced with the job file path.
  Job file: key=value lines.  Result file: key=value lines, last line END=1.
  Only the Altium scripting API is used to modify libraries. Nothing is ever deleted or overwritten. }

const
  JOB_FILE = '{{JOB_FILE}}';
  RESULT_FILE = '{{RESULT_FILE}}';

var
  Job, Res, FpOld, FpNew, Opened : TStringList;
  Step : String;
  Failed : Boolean;

function JobVal(Key : String) : String;
begin
  Result := Trim(Job.Values[Key]);
end;

procedure Fail(Msg : String);
begin
  Res.Add('ERROR=' + Msg);
  Failed := True;
end;

function FindSchComp(Lib : ISch_Lib; Name : String) : ISch_Component;
var
  It : ISch_Iterator;
  C : ISch_Component;
begin
  Result := nil;
  It := Lib.SchLibIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eSchComponent));
  C := It.FirstSchObject;
  while (C <> nil) and (Result = nil) do
  begin
    if UpperCase(C.LibReference) = UpperCase(Name) then Result := C;
    C := It.NextSchObject;
  end;
  Lib.SchIterator_Destroy(It);
end;

function FindFp(Lib : IPCB_Library; Name : String) : IPCB_LibComponent;
var
  It : IPCB_LibraryIterator;
  F : IPCB_LibComponent;
begin
  Result := nil;
  It := Lib.LibraryIterator_Create;
  It.SetState_FilterAll;
  F := It.FirstPCBObject;
  while (F <> nil) and (Result = nil) do
  begin
    if UpperCase(F.Name) = UpperCase(Name) then Result := F;
    F := It.NextPCBObject;
  end;
  Lib.LibraryIterator_Destroy(It);
end;

function UniqueSchName(Lib : ISch_Lib; Base : String) : String;
var
  i : Integer;
begin
  i := 1;
  Result := Base + '_' + IntToStr(i);
  while FindSchComp(Lib, Result) <> nil do
  begin
    Inc(i);
    Result := Base + '_' + IntToStr(i);
  end;
end;

function UniqueFpName(Lib : IPCB_Library; Base : String) : String;
var
  i : Integer;
begin
  i := 1;
  Result := Base + '_' + IntToStr(i);
  while FindFp(Lib, Result) <> nil do
  begin
    Inc(i);
    Result := Base + '_' + IntToStr(i);
  end;
end;

{ Note: DelphiScript requires routines to be declared before they are called. }
function CountFp(Fp : IPCB_LibComponent) : Integer;
var
  It : IPCB_GroupIterator;
  Prim : IPCB_Primitive;
begin
  Result := 0;
  It := Fp.GroupIterator_Create;
  It.SetState_FilterAll;
  Prim := It.FirstPCBObject;
  while Prim <> nil do
  begin
    Inc(Result);
    Prim := It.NextPCBObject;
  end;
  Fp.GroupIterator_Destroy(It);
end;

procedure CopyFootprint(SrcLib, DestLib : IPCB_Library; SrcFp : IPCB_LibComponent; NewName : String);
var
  NewFp : IPCB_LibComponent;
  It : IPCB_GroupIterator;
  Prim, C : IPCB_Primitive;
  dx, dy : Integer;
begin
  dx := DestLib.Board.XOrigin - SrcLib.Board.XOrigin;
  dy := DestLib.Board.YOrigin - SrcLib.Board.YOrigin;
  NewFp := PCBServer.CreatePCBLibComp;
  NewFp.Name := NewName;
  NewFp.Description := SrcFp.Description;
  NewFp.Height := SrcFp.Height;
  DestLib.RegisterComponent(NewFp);
  PCBServer.PreProcess;
  It := SrcFp.GroupIterator_Create;
  It.SetState_FilterAll;
  Prim := It.FirstPCBObject;
  while Prim <> nil do
  begin
    C := Prim.Replicate;
    if (dx <> 0) or (dy <> 0) then C.MoveByXY(dx, dy);
    NewFp.AddPCBObject(C);
    PCBServer.SendMessageToRobots(NewFp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, C.I_ObjectAddress);
    Prim := It.NextPCBObject;
  end;
  SrcFp.GroupIterator_Destroy(It);
  PCBServer.PostProcess;
  DestLib.CurrentComponent := NewFp;
end;

function CountBodies(Fp : IPCB_LibComponent) : Integer;
var
  It : IPCB_GroupIterator;
  Prim : IPCB_Primitive;
begin
  Result := 0;
  It := Fp.GroupIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
  Prim := It.FirstPCBObject;
  while Prim <> nil do
  begin
    Inc(Result);
    Prim := It.NextPCBObject;
  end;
  Fp.GroupIterator_Destroy(It);
end;

{ Attach a STEP file as 3D body to a footprint that has none (SamacSys ships the STEP separately).
  Body creation follows EasyEDA-Importer's verified BuildFootprint code. Placed at the footprint
  origin, unrotated - the user must check the 3D view.
  Called on the SOURCE footprint (a temporary extracted copy, never saved): a body created by
  script directly in an existing destination library reloads 50000 mil away at absolute (0,0)
  (verified AD26), while bodies brought over by the normal copy (Replicate) reload correctly. }
function AttachStep(Lib : IPCB_Library; Fp : IPCB_LibComponent; StepFile : String) : Boolean;
var
  Body : IPCB_ComponentBody;
  Model : IPCB_Model;
begin
  Result := False;
  if (StepFile = '') or (not FileExists(StepFile)) then Exit;
  if CountBodies(Fp) > 0 then Exit;
  PCBServer.PreProcess;
  try
    Body := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
    try
      Body.BodyProjection := eBoardSide_Top;
      Body.Layer := eMechanical13;
    except
    end;
    Model := Body.ModelFactory_FromFilename(StepFile, False);
    if Model = nil then
    begin
      Res.Add('WARNING=Altium could not read the 3D model ' + ExtractFileName(StepFile) + '.');
      Exit;
    end;
    try
      Body.SetState_FromModel;
    except
    end;
    Model.SetState(0, 0, 0, 0);
    Body.Model := Model;
    try
      Body.SetState_FromModel;
    except
    end;
    Body.MoveByXY(Lib.Board.XOrigin, Lib.Board.YOrigin);   { model arrives at absolute (0,0) }
    Fp.AddPCBObject(Body);       { footprint only: also adding to Lib.Board duplicated it }
    PCBServer.SendMessageToRobots(Fp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Body.I_ObjectAddress);
    Result := True;
  finally
    PCBServer.PostProcess;
  end;
end;

{ Returns the destination name for a source footprint (copies it if needed). '' if not found. }
function ProvideFootprint(SrcLib, DestLib : IPCB_Library; Name : String) : String;
var
  SrcFp, NewFp : IPCB_LibComponent;
  NewName : String;
  idx, nNew, nSrc : Integer;
  Added3D : Boolean;
begin
  Result := '';
  idx := FpOld.IndexOf(UpperCase(Name));
  if idx >= 0 then
  begin
    Result := FpNew[idx];
    Exit;
  end;
  SrcFp := FindFp(SrcLib, Name);
  if SrcFp = nil then
  begin
    Res.Add('WARNING=Footprint ' + Name + ' referenced by the symbol is not in the source PcbLib.');
    Exit;
  end;
  NewName := SrcFp.Name;
  if FindFp(DestLib, NewName) <> nil then
  begin
    if JobVal('OnConflict') = 'rename' then
    begin
      NewName := UniqueFpName(DestLib, SrcFp.Name);
      Res.Add('WARNING=Footprint ' + SrcFp.Name + ' already exists in destination; imported as ' + NewName + '.');
    end
    else
    begin
      Res.Add('WARNING=Footprint ' + NewName + ' already exists in destination and was NOT replaced. The symbol is linked to the EXISTING footprint - verify it is the same package.');
      Res.Add('EXISTING_FOOTPRINT=' + NewName);
      FpOld.Add(UpperCase(Name));
      FpNew.Add(NewName);
      Result := NewName;
      Exit;
    end;
  end;
  Added3D := AttachStep(SrcLib, SrcFp, JobVal('StepFile'));     { into the temp source copy }
  CopyFootprint(SrcLib, DestLib, SrcFp, NewName);
  NewFp := FindFp(DestLib, NewName);
  if NewFp = nil then
  begin
    Fail('Footprint ' + NewName + ' was not created. Library NOT saved.');
    Exit;
  end;
  nNew := CountFp(NewFp);
  nSrc := CountFp(SrcFp);
  if nNew <> nSrc then
    Fail('Footprint ' + NewName + ' copy is incomplete: ' + IntToStr(nNew) + ' of ' + IntToStr(nSrc) +
         ' primitives. Library NOT saved.');
  Res.Add('VERIFY_FOOTPRINT=' + NewName + '|primitives ' + IntToStr(nNew) + '/' + IntToStr(nSrc));
  Res.Add('ADDED_FOOTPRINT=' + NewName);
  if Added3D then Res.Add('ADDED_3D=' + NewName + '|' + ExtractFileName(JobVal('StepFile')));
  FpOld.Add(UpperCase(Name));
  FpNew.Add(NewName);
  Result := NewName;
end;

procedure SetParam(Comp : ISch_Component; Name, Value : String);
var
  It : ISch_Iterator;
  P, Found : ISch_Parameter;
begin
  Found := nil;
  It := Comp.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eParameter));
  P := It.FirstSchObject;
  while P <> nil do
  begin
    if (Found = nil) and (UpperCase(P.Name) = UpperCase(Name)) then Found := P;
    P := It.NextSchObject;
  end;
  Comp.SchIterator_Destroy(It);
  if Found <> nil then
  begin
    if (Value <> '') and (Found.Text <> Value) then
    begin
      Res.Add('PARAM=' + Name + ' changed from "' + Found.Text + '" to "' + Value + '"');
      Found.Text := Value;
    end;
  end
  else
  begin
    P := SchServer.SchObjectFactory(eParameter, eCreate_Default);
    P.Name := Name;
    P.Text := Value;
    P.IsHidden := True;
    Comp.AddSchObject(P);
    Res.Add('PARAM=' + Name + ' added "' + Value + '"');
  end;
end;

procedure PinCheck(Comp : ISch_Component; Fp : IPCB_LibComponent);
var
  Pins, Pads : TStringList;
  It : ISch_Iterator;
  Pin : ISch_Pin;
  GIt : IPCB_GroupIterator;
  Pad : IPCB_Primitive;
  i : Integer;
  Missing, Extra : String;
begin
  Pins := TStringList.Create;
  Pads := TStringList.Create;
  It := Comp.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePin));
  Pin := It.FirstSchObject;
  while Pin <> nil do
  begin
    if Pins.IndexOf(Pin.Designator) < 0 then Pins.Add(Pin.Designator);
    Pin := It.NextSchObject;
  end;
  Comp.SchIterator_Destroy(It);
  GIt := Fp.GroupIterator_Create;
  GIt.AddFilter_ObjectSet(MkSet(ePadObject));
  Pad := GIt.FirstPCBObject;
  while Pad <> nil do
  begin
    if (Trim(Pad.Name) <> '') and (Pads.IndexOf(Pad.Name) < 0) then Pads.Add(Pad.Name);
    Pad := GIt.NextPCBObject;
  end;
  Fp.GroupIterator_Destroy(GIt);
  Missing := '';
  Extra := '';
  for i := 0 to Pins.Count - 1 do
    if Pads.IndexOf(Pins[i]) < 0 then Missing := Missing + Pins[i] + ' ';
  for i := 0 to Pads.Count - 1 do
    if Pins.IndexOf(Pads[i]) < 0 then Extra := Extra + Pads[i] + ' ';
  Res.Add('PINCHECK=' + Comp.LibReference + '|' + Fp.Name + '|' + IntToStr(Pins.Count) + '|' +
          IntToStr(Pads.Count) + '|' + Trim(Missing) + '|' + Trim(Extra));
  Pins.Free;
  Pads.Free;
end;

{ Counts child objects of a symbol (PinsOnly) or of a footprint. Used to verify copies. }
function CountSch(Comp : ISch_Component; PinsOnly : Boolean) : Integer;
var
  It : ISch_Iterator;
  Obj : ISch_GraphicalObject;
begin
  Result := 0;
  It := Comp.SchIterator_Create;
  Obj := It.FirstSchObject;
  while Obj <> nil do
  begin
    if (not PinsOnly) or (Obj.ObjectId = ePin) then Inc(Result);
    Obj := It.NextSchObject;
  end;
  Comp.SchIterator_Destroy(It);
end;

{ ISch_Component.Replicate copies parameters/implementations but NOT pins and graphics
  (verified on AD26). Copy every child object type that the replica is missing. }
function CopySymbol(SrcComp : ISch_Component) : ISch_Component;
var
  Ids : TStringList;
  It : ISch_Iterator;
  Obj : ISch_GraphicalObject;
begin
  Result := SrcComp.Replicate;
  Ids := TStringList.Create;
  It := Result.SchIterator_Create;
  Obj := It.FirstSchObject;
  while Obj <> nil do
  begin
    if Ids.IndexOf(IntToStr(Obj.ObjectId)) < 0 then Ids.Add(IntToStr(Obj.ObjectId));
    Obj := It.NextSchObject;
  end;
  Result.SchIterator_Destroy(It);
  It := SrcComp.SchIterator_Create;
  Obj := It.FirstSchObject;
  while Obj <> nil do
  begin
    if Ids.IndexOf(IntToStr(Obj.ObjectId)) < 0 then Result.AddSchObject(Obj.Replicate);
    Obj := It.NextSchObject;
  end;
  SrcComp.SchIterator_Destroy(It);
  Ids.Free;
end;

function LinkLocation : String;
begin
  if JobVal('LinkMode') = 'path' then Result := JobVal('DestPcbLib')
  else if JobVal('LinkMode') = 'any' then Result := ''
  else Result := ExtractFileName(JobVal('DestPcbLib'));
end;

procedure ImportComponent(SrcComp : ISch_Component; SrcPcb, DestPcb : IPCB_Library; DestSch : ISch_Lib; ApplyParams : Boolean);
var
  Refs, Linked : TStringList;
  It, LIt : IPCB_LibraryIterator;
  ImplIt : ISch_Iterator;
  Impl : ISch_Implementation;
  F : IPCB_LibComponent;
  NewComp : ISch_Component;
  NewName, D, Loc : String;
  i, n, idx : Integer;
begin
  Step := 'conflict check ' + SrcComp.LibReference;
  NewName := SrcComp.LibReference;
  if FindSchComp(DestSch, NewName) <> nil then
  begin
    if JobVal('OnConflict') = 'rename' then
      NewName := UniqueSchName(DestSch, SrcComp.LibReference)
    else
    begin
      Res.Add('SKIPPED_SYMBOL=' + NewName + ' (already exists in destination SchLib)');
      Exit;
    end;
  end;

  Step := 'footprints ' + SrcComp.LibReference;
  Refs := TStringList.Create;
  ImplIt := SrcComp.SchIterator_Create;
  ImplIt.AddFilter_ObjectSet(MkSet(eImplementation));
  Impl := ImplIt.FirstSchObject;
  while Impl <> nil do
  begin
    if UpperCase(Impl.ModelType) = 'PCBLIB' then Refs.Add(Impl.ModelName);
    Impl := ImplIt.NextSchObject;
  end;
  SrcComp.SchIterator_Destroy(ImplIt);
  if Refs.Count = 0 then
  begin
    LIt := SrcPcb.LibraryIterator_Create;
    LIt.SetState_FilterAll;
    F := LIt.FirstPCBObject;
    while F <> nil do
    begin
      Refs.Add(F.Name);
      F := LIt.NextPCBObject;
    end;
    SrcPcb.LibraryIterator_Destroy(LIt);
    Res.Add('WARNING=Symbol ' + SrcComp.LibReference + ' had no footprint link; linked all ' + IntToStr(Refs.Count) + ' footprint(s) from the source PcbLib.');
  end;
  Linked := TStringList.Create;
  for i := 0 to Refs.Count - 1 do
    Linked.Add(ProvideFootprint(SrcPcb, DestPcb, Refs[i]));

  Step := 'symbol ' + SrcComp.LibReference;
  NewComp := CopySymbol(SrcComp);
  if NewName <> SrcComp.LibReference then
  begin
    NewComp.LibReference := NewName;
    Res.Add('WARNING=Symbol ' + SrcComp.LibReference + ' already exists in destination; imported as ' + NewName + '.');
  end;

  Step := 'link ' + NewName;
  Loc := LinkLocation;
  n := 0;
  ImplIt := NewComp.SchIterator_Create;
  ImplIt.AddFilter_ObjectSet(MkSet(eImplementation));
  Impl := ImplIt.FirstSchObject;
  while Impl <> nil do
  begin
    if UpperCase(Impl.ModelType) = 'PCBLIB' then
    begin
      idx := FpOld.IndexOf(UpperCase(Impl.ModelName));
      if idx >= 0 then
      begin
        Impl.ModelName := FpNew[idx];
        Impl.ClearAllDatafileLinks;
        Impl.AddDataFileLink(FpNew[idx], Loc, 'PCBLIB');
        Res.Add('LINK=' + NewName + '|' + FpNew[idx] + '|' + Loc);
        Inc(n);
      end;
    end;
    Impl := ImplIt.NextSchObject;
  end;
  NewComp.SchIterator_Destroy(ImplIt);
  if n = 0 then
    for i := 0 to Linked.Count - 1 do
      if Linked[i] <> '' then
      begin
        Impl := NewComp.AddSchImplementation;
        Impl.ModelName := Linked[i];
        Impl.ModelType := 'PCBLIB';
        Impl.SetState_IdentifierString('');
        Impl.AddDataFileLink(Linked[i], Loc, 'PCBLIB');
        Impl.IsCurrent := (n = 0);
        Res.Add('LINK=' + NewName + '|' + Linked[i] + '|' + Loc);
        Inc(n);
      end;
  if n = 0 then Res.Add('WARNING=Symbol ' + NewName + ' is NOT linked to any footprint.');

  Step := 'parameters ' + NewName;
  if ApplyParams then
  begin
    D := JobVal('Description');
    if D <> '' then NewComp.ComponentDescription := D;
    for i := 1 to StrToInt(JobVal('ParamCount')) do
      SetParam(NewComp, JobVal('Param' + IntToStr(i) + 'Name'), JobVal('Param' + IntToStr(i) + 'Value'));
  end;

  Step := 'add symbol ' + NewName;
  DestSch.AddSchComponent(NewComp);
  SchServer.RobotManager.SendMessage(DestSch.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, NewComp.I_ObjectAddress);
  Res.Add('ADDED_SYMBOL=' + NewName);

  Step := 'verify ' + NewName;
  if CountSch(NewComp, True) <> CountSch(SrcComp, True) then
    Fail('Symbol ' + NewName + ' copy is incomplete: ' + IntToStr(CountSch(NewComp, True)) + ' of ' +
         IntToStr(CountSch(SrcComp, True)) + ' pins. Library NOT saved.');
  if CountSch(NewComp, False) < CountSch(SrcComp, False) then
    Fail('Symbol ' + NewName + ' copy is incomplete: ' + IntToStr(CountSch(NewComp, False)) + ' of ' +
         IntToStr(CountSch(SrcComp, False)) + ' objects. Library NOT saved.');
  Res.Add('VERIFY_SYMBOL=' + NewName + '|pins ' + IntToStr(CountSch(NewComp, True)) + '/' +
          IntToStr(CountSch(SrcComp, True)) + '|objects ' + IntToStr(CountSch(NewComp, False)) + '/' +
          IntToStr(CountSch(SrcComp, False)));

  Step := 'pin check ' + NewName;
  for i := 0 to Linked.Count - 1 do
    if Linked[i] <> '' then
    begin
      F := FindFp(DestPcb, Linked[i]);
      if F <> nil then PinCheck(SrcComp, F);
    end;
  Refs.Free;
  Linked.Free;
end;

{ Close every document this job opened. After a failure this discards the unsaved partial changes. }
procedure CloseOpened;
var
  i : Integer;
  D : IServerDocument;
begin
  for i := 0 to Opened.Count - 1 do
  begin
    D := Client.GetDocumentByPath(Opened[i]);
    if D <> nil then Client.CloseDocument(D);
  end;
  Opened.Clear;
end;

{ AD26 (verified 2026-11-19): Altium refreshes its library search only AFTER the script run that
  saved a library has ended, from the saved document - so it must still be loaded then - and not
  if the library was open in an editor while being saved. Otherwise the new footprint stays
  "not found" until Altium restarts. So an import is separate runs:
  Action=prepare (close destinations open in an editor), Action=import with KeepOpen=1 (save, leave
  loaded), Action=finish (check the library search, close the hidden libraries with CloseDocs=1,
  reopen what the user had open). }
procedure DoPrepare;
var
  DSch, DPcb : IServerDocument;
begin
  Step := 'prepare';
  DSch := Client.GetDocumentByPath(JobVal('DestSchLib'));
  DPcb := Client.GetDocumentByPath(JobVal('DestPcbLib'));
  if ((DSch <> nil) and DSch.Modified) or ((DPcb <> nil) and DPcb.Modified) then
  begin
    Fail('A destination library is open in Altium with unsaved changes. Save or close it, then retry.');
    Exit;
  end;
  if DSch <> nil then
  begin
    Res.Add('SCH_WAS_OPEN=1');
    Client.CloseDocument(DSch);
  end;
  if DPcb <> nil then
  begin
    Res.Add('PCB_WAS_OPEN=1');
    Client.CloseDocument(DPcb);
  end;
end;

procedure DoFinish;
var
  i : Integer;
  Fp, Found : String;
  Path : WideString;
  D : IServerDocument;
begin
  Step := 'check library search';
  for i := 1 to StrToInt(JobVal('FootprintCount')) do
  begin
    Fp := JobVal('Footprint' + IntToStr(i));
    Path := '';
    Found := IntegratedLibraryManager.FindDatafileInStandardLibs(Fp, 'PCBLIB', JobVal('DestPcbLib'), True, Path);
    if Found <> '' then Res.Add('VERIFY_CACHE=' + Fp + '|ok')
    else Res.Add('VERIFY_CACHE=' + Fp + '|not found');
  end;
  Step := 'reopen';
  if JobVal('CloseDocs') = '1' then
  begin
    D := Client.GetDocumentByPath(JobVal('DestSchLib'));
    if (D <> nil) and (JobVal('ReopenSch') <> '1') then Client.CloseDocument(D);
    D := Client.GetDocumentByPath(JobVal('DestPcbLib'));
    if (D <> nil) and (JobVal('ReopenPcb') <> '1') then Client.CloseDocument(D);
  end;
  if JobVal('ReopenSch') = '1' then
  begin
    D := Client.OpenDocument('SCHLIB', JobVal('DestSchLib'));
    if D <> nil then Client.ShowDocument(D);
  end;
  if JobVal('ReopenPcb') = '1' then
  begin
    D := Client.OpenDocument('PCBLIB', JobVal('DestPcbLib'));
    if D <> nil then Client.ShowDocument(D);
  end;
end;

function OpenedByUs(Path : String; Kind : String) : IServerDocument;
begin
  Result := Client.GetDocumentByPath(Path);
  if Result = nil then
  begin
    Result := Client.OpenDocument(Kind, Path);
    Opened.Add(Path);
  end;
end;

procedure DoImport;
var
  DSch, DPcb : IServerDocument;
  SrcSch, DestSch : ISch_Lib;
  SrcPcb, DestPcb : IPCB_Library;
  It : ISch_Iterator;
  C : ISch_Component;
  i, Count : Integer;
  Want : String;
begin
  Step := 'check destination';
  DSch := Client.GetDocumentByPath(JobVal('DestSchLib'));
  DPcb := Client.GetDocumentByPath(JobVal('DestPcbLib'));
  if ((DSch <> nil) and DSch.Modified) or ((DPcb <> nil) and DPcb.Modified) then
  begin
    Fail('A destination library is open in Altium with unsaved changes. Save or close it, then retry.');
    Exit;
  end;

  Step := 'open libraries';
  if (DSch <> nil) or (DPcb <> nil) then
    Res.Add('WARNING=A destination library was still open in Altium; Altium may not find the new footprint until it is restarted.');
  OpenedByUs(JobVal('SrcSchLib'), 'SCHLIB');
  OpenedByUs(JobVal('SrcPcbLib'), 'PCBLIB');
  DSch := OpenedByUs(JobVal('DestSchLib'), 'SCHLIB');
  DPcb := OpenedByUs(JobVal('DestPcbLib'), 'PCBLIB');
  SrcSch := SchServer.GetSchDocumentByPath(JobVal('SrcSchLib'));
  DestSch := SchServer.GetSchDocumentByPath(JobVal('DestSchLib'));
  SrcPcb := PCBServer.GetPCBLibraryByPath(JobVal('SrcPcbLib'));
  DestPcb := PCBServer.GetPCBLibraryByPath(JobVal('DestPcbLib'));
  if (SrcSch = nil) or (DestSch = nil) or (SrcPcb = nil) or (DestPcb = nil) or (DSch = nil) or (DPcb = nil) then
  begin
    Fail('Could not open one of the libraries in Altium.');
    Exit;
  end;

  Step := 'count source symbols';
  Want := UpperCase(JobVal('Component'));
  Count := 0;
  It := SrcSch.SchLibIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eSchComponent));
  C := It.FirstSchObject;
  while C <> nil do
  begin
    if (Want = '') or (UpperCase(C.LibReference) = Want) then Inc(Count);
    C := It.NextSchObject;
  end;
  SrcSch.SchIterator_Destroy(It);
  if Count = 0 then
  begin
    if Want <> '' then Fail('Component ' + JobVal('Component') + ' is not in the source SchLib.')
    else Fail('The source SchLib contains no components.');
    Exit;
  end;
  if Count > 1 then
    Res.Add('WARNING=Source SchLib has ' + IntToStr(Count) + ' symbols; all imported, parameters NOT applied (they describe a single part).');

  SchServer.ProcessControl.PreProcess(DestSch, '');
  It := SrcSch.SchLibIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eSchComponent));
  C := It.FirstSchObject;
  while C <> nil do
  begin
    if (Want = '') or (UpperCase(C.LibReference) = Want) then
      ImportComponent(C, SrcPcb, DestPcb, DestSch, Count = 1);
    C := It.NextSchObject;
  end;
  SrcSch.SchIterator_Destroy(It);
  SchServer.ProcessControl.PostProcess(DestSch, '');
  DestSch.GraphicallyInvalidate;

  if Failed then
  begin
    Res.Add('ERROR=Nothing was saved. The destination libraries may be open in Altium with partial changes: close them WITHOUT saving.');
    Exit;
  end;

  Step := 'save';
  if not DSch.DoFileSave('') then Fail('Saving ' + JobVal('DestSchLib') + ' failed.');
  if not DPcb.DoFileSave('') then Fail('Saving ' + JobVal('DestPcbLib') + ' failed.');
end;

procedure DoExtract;
begin
  Step := 'extract intlib';
  IntegratedLibraryManager.ExtractSourcesToPath(JobVal('IntLib'), JobVal('OutDir'));
end;

procedure Run;
var
  i : Integer;
begin
  Job := TStringList.Create;
  Res := TStringList.Create;
  Opened := TStringList.Create;
  FpOld := TStringList.Create;
  FpNew := TStringList.Create;
  Failed := False;
  Step := 'load job';
  try
    try
      Job.LoadFromFile(JOB_FILE);
      if JobVal('Action') = 'extract' then DoExtract
      else if JobVal('Action') = 'prepare' then DoPrepare
      else if JobVal('Action') = 'finish' then DoFinish
      else DoImport;
    except
      Fail('Script exception during step: ' + Step);
    end;
  finally
    { Close what the job opened. After a failure this discards the unsaved partial changes. }
    try
      if (not Failed) and (JobVal('KeepOpen') = '1') then
      begin
        { keep the saved destinations loaded; Action=finish closes them }
        i := Opened.IndexOf(JobVal('DestSchLib'));
        if i >= 0 then Opened.Delete(i);
        i := Opened.IndexOf(JobVal('DestPcbLib'));
        if i >= 0 then Opened.Delete(i);
      end;
      CloseOpened;
    except
      Res.Add('WARNING=Could not close the libraries after the job.');
    end;
    if Failed then Res.Insert(0, 'STATUS=ERROR') else Res.Insert(0, 'STATUS=OK');
    Res.Add('END=1');
    Res.SaveToFile(RESULT_FILE);
    Opened.Free;
    Job.Free;
    Res.Free;
    FpOld.Free;
    FpNew.Free;
  end;
end;

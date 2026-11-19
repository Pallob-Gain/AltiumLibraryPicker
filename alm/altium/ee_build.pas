{ Altium Library Manager - EasyEDA part builder (DelphiScript, AD26).
  The routines below are taken unchanged from EasyEDA-Importer (EasyEDA_Importer.pas,
  github.com/hiteshwar/EasyEDA-Importer), used with the owner's permission, except where marked
  "ALM:". Dialog, settings, helper launching, library installing and sheet placement were removed;
  a non-interactive Run entry (bottom) builds ONE part file into NEW staging libraries.
  Generated per job by alm/altium/bridge.py ({{JOB_FILE}}, {{RESULT_FILE}} replaced). }

const
  JOB_FILE = '{{JOB_FILE}}';
  RESULT_FILE = '{{RESULT_FILE}}';
const
  cLibFolder          = '';      // empty = <script folder>\Libraries, or a full folder path
  cReplaceSymbols     = True;    // re-importing a part replaces its symbol
  cReplaceFootprints  = False;   // False = re-use an existing footprint with the same name
  cFillBodyRect       = True;    // give symbol rectangles the usual Altium yellow fill
  cUsePython          = False;   // True = use ee_convert.py (needs Python 3) instead of PowerShell
  cSymLineColor       = $000080; // dark red  (BGR)
  cSymFillColor       = $B0FFFF; // light yellow (BGR)

var
  gParts      : TStringList;
  gScriptDir  : String;
  gLibDir     : String;
  gErr        : String;
  gReport     : TStringList;
  gStartDoc   : IServerDocument;
  gNewSch     : Boolean;
  gNewPcb     : Boolean;
  gStartSheet : String;
  gCurPart    : Integer;
  gMapKeys    : TStringList;
  gMapVals    : TStringList;
  gOnlyListed : Boolean;
  gLastFp     : String;
  gLastPcbPath : String;
  gPcbLib     : IPCB_Library;
  gPrimCount  : Integer;
  gPadCount   : Integer;

{------------------------------------------------------------------ helpers ---}
procedure SplitTabs(S : String);
var
  P    : Integer;
  Rest : String;
begin
  gParts.Clear;
  Rest := S;
  P := Pos(#9, Rest);
  while P > 0 do
  begin
    gParts.Add(Copy(Rest, 1, P - 1));
    Rest := Copy(Rest, P + 1, Length(Rest));
    P := Pos(#9, Rest);
  end;
  gParts.Add(Rest);
end;

function FStr(I : Integer) : String;
begin
  if I < gParts.Count then Result := gParts.Strings[I] else Result := '';
end;

function FInt(I : Integer) : Integer;
begin
  Result := 0;
  if I < gParts.Count then
  begin
    try
      Result := StrToInt(Trim(gParts.Strings[I]));
    except
      Result := 0;
    end;
  end;
end;

{ all lengths in the part file are integer 1/100 mil }
function FCoord(I : Integer) : Integer;
begin
  Result := MilsToCoord(FInt(I) / 100);
end;

function FAngle(I : Integer) : Double;
begin
  Result := FInt(I) / 1000;
end;

function JoinPath(A, B : String) : String;
begin
  if (Length(A) > 0) and (Copy(A, Length(A), 1) <> '\') then Result := A + '\' + B
  else Result := A + B;
end;

function CleanLcsc(S : String) : String;
var
  I : Integer;
  Ch : String;
begin
  Result := '';
  S := UpperCase(Trim(S));
  for I := 1 to Length(S) do
  begin
    Ch := Copy(S, I, 1);
    if ((Ch >= '0') and (Ch <= '9')) or (Ch = 'C') then Result := Result + Ch;
  end;
  if (Length(Result) > 0) and (Copy(Result, 1, 1) <> 'C') then Result := 'C' + Result;
  if Result = 'C' then Result := '';
end;

function LayerOf(S : String) : Integer;
begin
  Result := eMechanical15;
  if S = 'TOP' then Result := eTopLayer
  else if S = 'BOTTOM' then Result := eBottomLayer
  else if S = 'TOPOVERLAY' then Result := eTopOverlay
  else if S = 'BOTTOMOVERLAY' then Result := eBottomOverlay
  else if S = 'TOPPASTE' then Result := eTopPaste
  else if S = 'BOTTOMPASTE' then Result := eBottomPaste
  else if S = 'TOPSOLDER' then Result := eTopSolder
  else if S = 'BOTTOMSOLDER' then Result := eBottomSolder
  else if S = 'MULTI' then Result := eMultiLayer
  else if S = 'MECH1' then Result := eMechanical1
  else if S = 'MECH9' then Result := eMechanical9
  else if S = 'MECH10' then Result := eMechanical10
  else if S = 'MECH11' then Result := eMechanical11
  else if S = 'MECH13' then Result := eMechanical13
  else if S = 'MECH14' then Result := eMechanical14
  else if S = 'MECH15' then Result := eMechanical15
  else if S = 'MECH16' then Result := eMechanical16;
end;

function RotOf(A : Integer) : Integer;
begin
  Result := eRotate0;
  if A = 90 then Result := eRotate90
  else if A = 180 then Result := eRotate180
  else if A = 270 then Result := eRotate270;
end;

function ElecOf(E : Integer) : Integer;
begin
  Result := eElectricPassive;
  if E = 0 then Result := eElectricInput
  else if E = 1 then Result := eElectricIO
  else if E = 2 then Result := eElectricOutput
  else if E = 3 then Result := eElectricOpenCollector
  else if E = 5 then Result := eElectricHiZ
  else if E = 6 then Result := eElectricOpenEmitter
  else if E = 7 then Result := eElectricPower;
end;

{ value of a META record, e.g. MetaOf(Lines, 'NAME') }
function MetaOf(Lines : TStringList; Key : String) : String;
var
  I : Integer;
begin
  Result := '';
  for I := 0 to Lines.Count - 1 do
  begin
    SplitTabs(Lines.Strings[I]);
    if (FStr(0) = 'META') and (FStr(1) = Key) then
    begin
      Result := FStr(2);
      Exit;
    end;
  end;
end;


{------------------------------------------------- settings & parameter map ---}
{ Parameter-name key used for matching: upper case, letters and digits only,
  so 'ManufacturerPart', 'Manufacturer Part' and 'manufacturer_part' are equal. }
function NormKey(S : String) : String;
var
  I  : Integer;
  Ch : String;
begin
  Result := '';
  S := UpperCase(S);
  for I := 1 to Length(S) do
  begin
    Ch := Copy(S, I, 1);
    if ((Ch >= 'A') and (Ch <= 'Z')) or ((Ch >= '0') and (Ch <= '9')) then Result := Result + Ch;
  end;
end;

function DefaultParamMap : String;
begin
  Result :=
    '# New name = name(s) used by EasyEDA (comma separated)' + #13#10 +
    'Manufacturer = Manufacturer, Mfr, BOM_Manufacturer' + #13#10 +
    'Manufacturer Part Number = Manufacturer Part, ManufacturerPart, Manufacturer Part Number, MPN, Mfr Part, BOM_Manufacturer Part' + #13#10 +
    'JLCPCB Part Number = Supplier Part, Supplier Part Number, LCSC Part, LCSC Part Number, JLCPCB Part, BOM_Supplier Part' + #13#10 +
    'Supplier = Supplier, BOM_Supplier' + #13#10 +
    'JLCPCB Part Class = JLCPCB Part Class' + #13#10 +
    'Package = Package' + #13#10 +
    'Datasheet = Datasheet' + #13#10 +
    'Value = Value' + #13#10;
end;

procedure AddAlias(Alias, Target : String);
var
  K : String;
begin
  K := NormKey(Alias);
  if K <> '' then
  begin
    if gMapKeys.IndexOf(K) < 0 then
    begin
      gMapKeys.Add(K);
      gMapVals.Add(Target);
    end;
  end;
end;

{ Builds the alias table from text lines "New name = old, other old, ..." }
procedure BuildParamMap(Text : String);
var
  L     : TStringList;
  I, P  : Integer;
  T, Target, Rest : String;
begin
  gMapKeys.Clear;
  gMapVals.Clear;
  L := TStringList.Create;
  try
    L.Text := Text;
    for I := 0 to L.Count - 1 do
    begin
      T := Trim(L.Strings[I]);
      if (T <> '') and (Copy(T, 1, 1) <> '#') then
      begin
        P := Pos('=', T);
        if P > 1 then
        begin
          Target := Trim(Copy(T, 1, P - 1));
          Rest := Copy(T, P + 1, Length(T));
          AddAlias(Target, Target);
          P := Pos(',', Rest);
          while P > 0 do
          begin
            AddAlias(Trim(Copy(Rest, 1, P - 1)), Target);
            Rest := Copy(Rest, P + 1, Length(Rest));
            P := Pos(',', Rest);
          end;
          AddAlias(Trim(Rest), Target);
        end;
      end;
    end;
  finally
    L.Free;
  end;
end;

{ Final Altium name for an EasyEDA parameter; '' means "do not import it" }
function MapParam(Name : String) : String;
var
  I : Integer;
begin
  I := gMapKeys.IndexOf(NormKey(Name));
  if I >= 0 then
    Result := gMapVals.Strings[I]
  else if gOnlyListed then
    Result := ''
  else
    Result := Name;
end;


{-------------------------------------------------------------- libraries ---}
function OpenOrCreateLib(Kind, Path : String) : IServerDocument;
var
  Doc : IServerDocument;
begin
  Result := nil;
  if not FileExists(Path) then
  begin
    if Kind = 'SCHLIB' then gNewSch := True else gNewPcb := True;
    try
      Doc := CreateNewDocumentFromDocumentKind(Kind);
      Doc.SetFileName(Path);
      Doc.DoFileSave(Kind);
    except
    end;
  end;
  if not FileExists(Path) then
  begin
    gErr := 'Could not create ' + Path;   // ALM: no dialog (non-interactive)
    Exit;
  end;
  Doc := Client.OpenDocument(Kind, Path);   // ALM: not shown in an editor
  Result := Doc;
end;

procedure SaveDoc(Path, Kind : String);
var
  Doc : IServerDocument;
begin
  try
    Doc := Client.GetDocumentByPath(Path);
    if Doc <> nil then Doc.DoFileSave(Kind);
  except
    gReport.Add('  ! could not auto-save ' + Path + ' - please save it manually (Ctrl+S)');
  end;
end;


{-------------------------------------------------------------- footprint ---}
procedure AddPrim(Fp : IPCB_LibComponent; Prim : IPCB_Primitive);
begin
  // same as the original EasyEDALoader plugin: add to the library board AND to the footprint
  gPcbLib.Board.AddPCBObject(Prim);
  Fp.AddPCBObject(Prim);
  PCBServer.SendMessageToRobots(Fp.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Prim.I_ObjectAddress);
  Inc(gPrimCount);
end;

procedure AddTrack(Fp : IPCB_LibComponent; L : Integer; X1 : Integer; Y1 : Integer; X2 : Integer; Y2 : Integer; W : Integer);
var
  T : IPCB_Track;
begin
  T := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
  T.X1 := X1;
  T.Y1 := Y1;
  T.X2 := X2;
  T.Y2 := Y2;
  T.Width := W;
  T.Layer := L;
  AddPrim(Fp, T);
end;

function BuildFootprint(Lines : TStringList; PcbLibPath : String) : String;
var
  PcbLib   : IPCB_Library;
  Fp, Old  : IPCB_LibComponent;
  Pad      : IPCB_Pad;
  Via      : IPCB_Via;
  Arc      : IPCB_Arc;
  Region   : IPCB_Region;
  Contour  : IPCB_Contour;
  Body     : IPCB_ComponentBody;
  Model    : IPCB_Model;
  OX, OY, I, K, N, L : Integer;
  FpName, Kind, Lay, StepFile : String;
begin
  Result := '';
  FpName := MetaOf(Lines, 'FOOTPRINT');
  if FpName = '' then Exit;

  PcbLib := nil;
  try
    PcbLib := PCBServer.GetPCBLibraryByPath(PcbLibPath);
  except
  end;
  if PcbLib = nil then PcbLib := PCBServer.GetCurrentPCBLibrary;
  if PcbLib = nil then
  begin
    gErr := 'PCB library is not open';
    Exit;
  end;

  Old := PcbLib.GetComponentByName(FpName);
  if Old <> nil then
  begin
    if cReplaceFootprints = False then
    begin
      gReport.Add('  footprint "' + FpName + '" already in library - re-used (set cReplaceFootprints = True to refresh it)');
      Result := FpName;
      Exit;
    end;
  end;

  Fp := PCBServer.CreatePCBLibComp;
  if Old <> nil then Fp.Name := PcbLib.GetUniqueCompName(FpName) else Fp.Name := FpName;
  Fp.Description := MetaOf(Lines, 'DESC');
  PcbLib.RegisterComponent(Fp);
  PcbLib.CurrentComponent := Fp;
  if Old <> nil then
  begin
    // replace: drop the old footprint now that the new one is current
    try
      PcbLib.RemoveComponent(Old);
      Fp.Name := FpName;
    except
      FpName := Fp.Name;
    end;
  end;

  gPcbLib := PcbLib;
  gPrimCount := 0;
  gPadCount := 0;
  OX := PcbLib.Board.XOrigin;
  OY := PcbLib.Board.YOrigin;

  PCBServer.PreProcess;
  try
  for I := 0 to Lines.Count - 1 do
  begin
    SplitTabs(Lines.Strings[I]);
    Kind := FStr(0);

    if Kind = 'FPAD' then
    begin
      Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
      Pad.Mode := ePadMode_Simple;
      Pad.X := OX + FCoord(3);
      Pad.Y := OY + FCoord(4);
      Pad.TopXSize := FCoord(5);
      Pad.TopYSize := FCoord(6);
      if FStr(2) = 'RECT' then Pad.TopShape := eRectangular else Pad.TopShape := eRounded;
      Lay := FStr(8);
      if Lay = 'MULTI' then
      begin
        Pad.Layer := eMultiLayer;
        Pad.HoleSize := FCoord(9);
        if FInt(12) = 1 then Pad.Plated := True else Pad.Plated := False;
        if FInt(10) > 0 then
        begin
          Pad.HoleType := eSlotHole;
          Pad.HoleWidth := FCoord(10);          // slot length
          Pad.HoleRotation := FAngle(11);
        end;
      end
      else
      begin
        if Lay = 'BOTTOM' then Pad.Layer := eBottomLayer else Pad.Layer := eTopLayer;
        Pad.HoleSize := 0;
      end;
      Pad.Rotation := FAngle(7);
      Pad.Name := FStr(1);
      AddPrim(Fp, Pad);
      Inc(gPadCount);
    end
    else if Kind = 'FHOLE' then
    begin
      Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
      Pad.Mode := ePadMode_Simple;
      Pad.X := OX + FCoord(1);
      Pad.Y := OY + FCoord(2);
      Pad.TopXSize := FCoord(3);
      Pad.TopYSize := FCoord(3);
      Pad.TopShape := eRounded;
      Pad.Layer := eMultiLayer;
      Pad.HoleSize := FCoord(3);
      Pad.Plated := False;
      Pad.Name := '';
      AddPrim(Fp, Pad);
    end
    else if Kind = 'FVIA' then
    begin
      Via := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
      Via.X := OX + FCoord(1);
      Via.Y := OY + FCoord(2);
      Via.Size := FCoord(3);
      Via.HoleSize := FCoord(4);
      Via.LowLayer := eBottomLayer;
      Via.HighLayer := eTopLayer;
      AddPrim(Fp, Via);
    end
    else if Kind = 'FTRACK' then
    begin
      AddTrack(Fp, LayerOf(FStr(1)), OX + FCoord(2), OY + FCoord(3), OX + FCoord(4), OY + FCoord(5), FCoord(6));
    end
    else if Kind = 'FARC' then
    begin
      Arc := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
      Arc.XCenter := OX + FCoord(2);
      Arc.YCenter := OY + FCoord(3);
      Arc.Radius := FCoord(4);
      Arc.StartAngle := FAngle(5);
      Arc.EndAngle := FAngle(6);
      Arc.LineWidth := FCoord(7);
      Arc.Layer := LayerOf(FStr(1));
      AddPrim(Fp, Arc);
    end
    else if Kind = 'FREGION' then
    begin
      L := LayerOf(FStr(1));
      N := FInt(2);
      try
        Region := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
        Contour := PCBServer.PCBContourFactory;
        for K := 0 to N - 1 do
          Contour.AddPoint(OX + FCoord(3 + 2 * K), OY + FCoord(4 + 2 * K));
        Region.SetOutlineContour(Contour);
        Region.Layer := L;
        AddPrim(Fp, Region);
      except
        // fall back to an outline made of thin tracks
        for K := 0 to N - 1 do
          AddTrack(Fp, L, OX + FCoord(3 + 2 * K), OY + FCoord(4 + 2 * K),
                   OX + FCoord(3 + 2 * ((K + 1) mod N)), OY + FCoord(4 + 2 * ((K + 1) mod N)), MilsToCoord(4));
      end;
    end
    else if Kind = 'F3D' then
    begin
      StepFile := JoinPath(JoinPath(gLibDir, '3D'), FStr(1));
      if FileExists(StepFile) then
      begin
        try
          Body := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
          try
            Body.BodyProjection := eBoardSide_Top;
            Body.Layer := eMechanical13;
          except
          end;
          Model := Body.ModelFactory_FromFilename(StepFile, False);
          if Model <> nil then
          begin
            try
              Body.SetState_FromModel;
            except
            end;
            Model.SetState(FAngle(5), FAngle(6), FAngle(7), FCoord(4));
            Body.Model := Model;
            try
              Body.SetState_FromModel;   // refresh outline after rotation
            except
            end;
            Body.MoveByXY(OX + FCoord(2), OY + FCoord(3));
            AddPrim(Fp, Body);
          end
          else gReport.Add('  ! Altium could not read ' + StepFile);
        except
          gReport.Add('  ! 3D model could not be attached (' + StepFile + ')');
        end;
      end
      else gReport.Add('  ! STEP file missing: ' + StepFile);
    end
    else if Kind = 'WARN' then
      gReport.Add('  ! ' + FStr(1));
  end;
  finally
    PCBServer.PostProcess;
  end;
  PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Fp.I_ObjectAddress);
  PcbLib.CurrentComponent := Fp;
  if gNewPcb then
  begin
    // a brand-new PcbLib contains an empty default footprint - remove it
    try
      Old := PcbLib.GetComponentByName('PCBCOMPONENT_1');
      if Old <> nil then
      begin
        PcbLib.RemoveComponent(Old);
      end;
    except
    end;
    gNewPcb := False;
  end;
  PcbLib.Board.ViewManager_FullUpdate;
  gReport.Add('  footprint "' + FpName + '": ' + IntToStr(gPadCount) + ' pads, ' +
              IntToStr(gPrimCount) + ' objects in total');
  Result := FpName;
end;

{----------------------------------------------------------------- symbol ---}
procedure AddSch(Comp : ISch_Component; Obj : ISch_GraphicalObject);
begin
  Obj.OwnerPartId := gCurPart;
  Obj.OwnerPartDisplayMode := Comp.DisplayMode;
  Comp.AddSchObject(Obj);
end;

procedure AddVertices(Poly : ISch_Polyline; First : Integer; N : Integer);
var
  K : Integer;
begin
  Poly.Location := Point(FCoord(First), FCoord(First + 1));
  for K := 1 to N do
  begin
    Poly.InsertVertex := K;
    Poly.SetState_Vertex(K, Point(FCoord(First + 2 * (K - 1)), FCoord(First + 2 * (K - 1) + 1)));
  end;
end;

function FindSymbol(SchLib : ISch_Lib; Name : String) : ISch_Component;
var
  It   : ISch_Iterator;
  Comp : ISch_Component;
begin
  Result := nil;
  It := SchLib.SchLibIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eSchComponent));
  try
    Comp := It.FirstSchObject;
    while (Comp <> nil) and (Result = nil) do
    begin
      if UpperCase(Comp.LibReference) = UpperCase(Name) then Result := Comp;
      Comp := It.NextSchObject;
    end;
  finally
    SchLib.SchIterator_Destroy(It);
  end;
end;

function BuildSymbol(Lines : TStringList; FpName, PcbLibPath, SchLibPath : String) : String;
var
  SchLib : ISch_Lib;
  Comp, Old : ISch_Component;
  Pin    : ISch_Pin;
  Obj    : ISch_GraphicalObject;
  Impl   : ISch_Implementation;
  Prm    : ISch_Parameter;
  I, N   : Integer;
  Name, Kind, PName : String;
  Added  : TStringList;
begin
  Result := '';
  SchLib := nil;
  try
    SchLib := SchServer.GetSchDocumentByPath(SchLibPath);
  except
  end;
  if SchLib = nil then SchLib := SchServer.GetCurrentSchDocument;
  if SchLib = nil then
  begin
    gErr := 'Schematic library is not open';
    Exit;
  end;

  Name := MetaOf(Lines, 'NAME');
  if Name = '' then Name := MetaOf(Lines, 'LCSC');

  Old := FindSymbol(SchLib, Name);
  if Old <> nil then
  begin
    if cReplaceSymbols then SchLib.RemoveSchComponent(Old)
    else
    begin
      gReport.Add('  symbol "' + Name + '" already in library - kept the existing one');
      Result := Name;
      Exit;
    end;
  end;

  Added := TStringList.Create;
  SchServer.ProcessControl.PreProcess(SchLib, '');
  try

  Comp := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
  Comp.CurrentPartID := 1;
  Comp.DisplayMode := 0;
  Comp.LibReference := Name;
  Comp.ComponentDescription := MetaOf(Lines, 'DESC');
  Comp.Designator.Text := MetaOf(Lines, 'PREFIX') + '?';
  try
    Comp.Comment.Text := Name;
  except
  end;
  SchLib.AddSchComponent(Comp);
  SchLib.CurrentSchComponent := Comp;
  gCurPart := 1;

  for I := 0 to Lines.Count - 1 do
  begin
    SplitTabs(Lines.Strings[I]);
    Kind := FStr(0);

    if Kind = 'SPART' then
    begin
      // multi-unit symbol: following objects belong to part N (A, B, C ...)
      gCurPart := FInt(1);
      if gCurPart < 1 then gCurPart := 1;
      if gCurPart > 1 then
      begin
        try
          Comp.PartCount := gCurPart;
        except
        end;
      end;
    end
    else if Kind = 'SPIN' then
    begin
      Pin := SchServer.SchObjectFactory(ePin, eCreate_Default);
      Pin.Location := Point(FCoord(1), FCoord(2));
      Pin.PinLength := FCoord(3);
      Pin.Orientation := RotOf(FInt(4));
      Pin.Electrical := ElecOf(FInt(5));
      if FInt(6) = 1 then Pin.ShowName := True else Pin.ShowName := False;
      if FInt(7) = 1 then Pin.ShowDesignator := True else Pin.ShowDesignator := False;
      if FInt(8) = 1 then
      begin
        try
          Pin.Symbol_OuterEdge := eDot;
        except
        end;
      end;
      if FInt(9) = 1 then
      begin
        try
          Pin.Symbol_InnerEdge := eClock;
        except
        end;
      end;
      Pin.Designator := FStr(10);
      Pin.Name := FStr(11);
      AddSch(Comp, Pin);
    end
    else if Kind = 'SRECT' then
    begin
      if FInt(6) > 0 then
      begin
        Obj := SchServer.SchObjectFactory(eRoundRectangle, eCreate_Default);
        Obj.CornerXRadius := FCoord(6);
        Obj.CornerYRadius := FCoord(7);
      end
      else Obj := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
      Obj.Location := Point(FCoord(1), FCoord(2));
      Obj.Corner := Point(FCoord(3), FCoord(4));
      Obj.LineWidth := eSmall;
      Obj.Color := cSymLineColor;
      Obj.AreaColor := cSymFillColor;
      if cFillBodyRect then
        Obj.IsSolid := True
      else if FInt(5) = 1 then
        Obj.IsSolid := True
      else
        Obj.IsSolid := False;
      AddSch(Comp, Obj);
    end
    else if Kind = 'SELLIPSE' then
    begin
      Obj := SchServer.SchObjectFactory(eEllipse, eCreate_Default);
      Obj.Location := Point(FCoord(1), FCoord(2));
      Obj.Radius := FCoord(3);
      Obj.SecondaryRadius := FCoord(4);
      Obj.LineWidth := eSmall;
      Obj.Color := cSymLineColor;
      Obj.AreaColor := cSymLineColor;
      if FInt(5) = 1 then Obj.IsSolid := True else Obj.IsSolid := False;
      AddSch(Comp, Obj);
    end
    else if Kind = 'SARC' then
    begin
      Obj := SchServer.SchObjectFactory(eArc, eCreate_Default);
      Obj.Location := Point(FCoord(1), FCoord(2));
      Obj.Radius := FCoord(3);
      Obj.StartAngle := FAngle(4);
      Obj.EndAngle := FAngle(5);
      Obj.LineWidth := eSmall;
      Obj.Color := cSymLineColor;
      AddSch(Comp, Obj);
    end
    else if Kind = 'SPOLY' then
    begin
      N := FInt(3);
      if FInt(1) = 1 then
      begin
        Obj := SchServer.SchObjectFactory(ePolygon, eCreate_Default);
        if FInt(2) = 1 then Obj.IsSolid := True else Obj.IsSolid := False;
        Obj.AreaColor := cSymLineColor;
      end
      else Obj := SchServer.SchObjectFactory(ePolyline, eCreate_Default);
      Obj.LineWidth := eSmall;
      Obj.Color := cSymLineColor;
      AddVertices(Obj, 4, N);
      AddSch(Comp, Obj);
    end
    else if Kind = 'STEXT' then
    begin
      Obj := SchServer.SchObjectFactory(eLabel, eCreate_Default);
      Obj.Location := Point(FCoord(1), FCoord(2));
      Obj.Text := FStr(5);
      Obj.Orientation := RotOf(((FInt(3) div 1000) div 90) * 90);
      Obj.Color := cSymLineColor;
      AddSch(Comp, Obj);
    end
    else if Kind = 'PARAM' then
    begin
      // rename / normalise with the user's parameter map; skip duplicates
      PName := MapParam(FStr(1));
      if PName <> '' then
      begin
        if Added.IndexOf(NormKey(PName)) < 0 then
        begin
          Added.Add(NormKey(PName));
          Prm := SchServer.SchObjectFactory(eParameter, eCreate_Default);
          Prm.Name := PName;
          Prm.Text := FStr(2);
          Prm.IsHidden := True;
          Comp.AddSchObject(Prm);
        end;
      end;
    end;
  end;

  if FpName <> '' then
  begin
    Impl := Comp.AddSchImplementation;
    Impl.ClearAllDatafileLinks;
    Impl.Description := 'EasyEDA footprint';
    Impl.ModelName := FpName;
    Impl.ModelType := 'PCBLIB';
    Impl.AddDataFileLink(FpName, PcbLibPath, 'PCBLIB');
    Impl.IsCurrent := True;
  end;

  finally
    SchServer.ProcessControl.PostProcess(SchLib, '');
    Added.Free;
  end;
  SchServer.RobotManager.SendMessage(SchLib.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, Comp.I_ObjectAddress);
  if gNewSch then
  begin
    // a brand-new SchLib contains an empty default component - remove it
    try
      Old := FindSymbol(SchLib, 'Component_1');
      if (Old <> nil) and (Old.I_ObjectAddress <> Comp.I_ObjectAddress) then SchLib.RemoveSchComponent(Old);
    except
    end;
    gNewSch := False;
  end;
  SchLib.CurrentSchComponent := Comp;
  SchLib.GraphicallyInvalidate;
  Result := Name;
end;

{=============================================================== ALM: entry ===}
var
  Job, Res : TStringList;
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

procedure CloseStage;
var
  D : IServerDocument;
begin
  D := Client.GetDocumentByPath(JobVal('StageSchLib'));
  if D <> nil then Client.CloseDocument(D);
  D := Client.GetDocumentByPath(JobVal('StagePcbLib'));
  if D <> nil then Client.CloseDocument(D);
end;

procedure BuildPart;
var
  Lines : TStringList;
  FpName, SymName, SchPath, PcbPath : String;
  I : Integer;
begin
  SchPath := JobVal('StageSchLib');
  PcbPath := JobVal('StagePcbLib');
  if FileExists(SchPath) or FileExists(PcbPath) then
  begin
    Fail('Staging library already exists (never reused): ' + SchPath);
    Exit;
  end;
  Lines := TStringList.Create;
  try
    Step := 'read part file';
    Lines.LoadFromFile(JobVal('PartFile'));
    Step := 'create staging PcbLib';
    gErr := '';
    if OpenOrCreateLib('PCBLIB', PcbPath) = nil then begin Fail(gErr); Exit; end;
    Step := 'build footprint';
    FpName := BuildFootprint(Lines, PcbPath);
    if FpName = '' then begin Fail('Footprint not built: ' + gErr); Exit; end;
    Step := 'create staging SchLib';
    if OpenOrCreateLib('SCHLIB', SchPath) = nil then begin Fail(gErr); Exit; end;
    Step := 'build symbol';
    SymName := BuildSymbol(Lines, FpName, PcbPath, SchPath);
    if SymName = '' then begin Fail('Symbol not built: ' + gErr); Exit; end;
    Step := 'save staging libraries';
    SaveDoc(PcbPath, 'PCBLIB');
    SaveDoc(SchPath, 'SCHLIB');
    Res.Add('BUILT_SYMBOL=' + SymName);
    Res.Add('BUILT_FOOTPRINT=' + FpName);
    Res.Add('BUILT_PADS=' + IntToStr(gPadCount));
    Res.Add('BUILT_PRIMITIVES=' + IntToStr(gPrimCount));
    for I := 0 to gReport.Count - 1 do
      if Copy(Trim(gReport.Strings[I]), 1, 1) = '!' then
        Res.Add('WARNING=' + Trim(Copy(Trim(gReport.Strings[I]), 2, 10000)))
      else
        Res.Add('INFO=' + Trim(gReport.Strings[I]));
  finally
    Lines.Free;
  end;
end;

procedure Run;
begin
  Job := TStringList.Create;
  Res := TStringList.Create;
  gParts := TStringList.Create;
  gReport := TStringList.Create;
  gMapKeys := TStringList.Create;
  gMapVals := TStringList.Create;
  Failed := False;
  gNewSch := False;
  gNewPcb := False;
  gCurPart := 1;
  Step := 'load job';
  try
    try
      Job.LoadFromFile(JOB_FILE);
      gLibDir := JobVal('LibDir');           // holds 3D\<uuid>.step
      gOnlyListed := True;                   // EasyEDA-Importer default parameter map
      BuildParamMap(DefaultParamMap);
      BuildPart;
    except
      Fail('Script exception during step: ' + Step);
    end;
  finally
    try
      CloseStage;
    except
    end;
    if Failed then Res.Insert(0, 'STATUS=ERROR') else Res.Insert(0, 'STATUS=OK');
    Res.Add('END=1');
    Res.SaveToFile(RESULT_FILE);
    Job.Free;
    Res.Free;
    gParts.Free;
    gReport.Free;
    gMapKeys.Free;
    gMapVals.Free;
  end;
end;
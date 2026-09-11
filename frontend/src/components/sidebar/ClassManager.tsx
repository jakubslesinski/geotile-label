import { useEffect, useMemo, useState } from "react";
import {
  VStack,
  HStack,
  Input,
  IconButton,
  Text,
  useColorModeValue,
} from "@chakra-ui/react";
import { MdAdd, MdDelete } from "react-icons/md";
import type { LabelClass } from "../../types";
import { useTranslation } from "react-i18next";
import ColorPicker, { CLASS_PALETTE } from "../common/ColorPicker";

interface Props {
  classes: LabelClass[];
  onAdd: (name: string, color: string) => void;
  onDelete: (classId: number) => void;
  onChangeColor: (classId: number, color: string) => void;
}

/**
 * Kolor, jaki dostanie kolejna klasa - ta sama reguła co w `create_class`: paleta domyślna
 * pod indeksem `max(id) + 1`, a po jej wyczerpaniu czerwony. Liczony z identyfikatorów, nie
 * z długości listy, bo po usunięciu klasy te dwie liczby się rozjeżdżają.
 */
function nextDefaultColor(classes: LabelClass[]): string {
  const nextId = classes.reduce((max, cls) => Math.max(max, cls.id), -1) + 1;
  return CLASS_PALETTE[nextId] ?? "#FF0000";
}

/**
 * Lista klas projektu.
 *
 * Komponent NIE ma własnego zwijania. Wcześniej miał — i ponieważ nagłówek z licznikiem
 * stał poza tym zwijaczem, a wywołujący (`ProjectDashboard`) owija całość swoim własnym
 * `Collapse` z przyciskiem „Pokaż/Ukryj", powstawały dwa niezależne sterowania jedną
 * listą. Zwinięcie tego wewnętrznego dawało widok „Klasy (53)" i pustkę pod spodem, czyli
 * dokładnie obraz wczytanej, ale pustej listy — bez żadnej wskazówki poza kierunkiem
 * małej strzałki. Zwijanie należy do wywołującego, tutaj byłoby duplikatem.
 */
export default function ClassManager({ classes, onAdd, onDelete, onChangeColor }: Props) {
  const { t } = useTranslation();
  const [newName, setNewName] = useState("");
  const textColor = useColorModeValue("navy.700", "white");

  const defaultColor = useMemo(() => nextDefaultColor(classes), [classes]);
  const [newColor, setNewColor] = useState(defaultColor);

  // Po dodaniu klasy (albo imporcie pliku) podpowiadany kolor przesuwa się na kolejny
  // z palety. Własny wybór użytkownika nie zmienia `defaultColor`, więc nie jest kasowany.
  useEffect(() => setNewColor(defaultColor), [defaultColor]);

  const handleAdd = () => {
    if (newName.trim()) {
      onAdd(newName.trim(), newColor);
      setNewName("");
    }
  };

  return (
    <VStack align="stretch" spacing={2}>
      <Text fontSize="sm" fontWeight="bold" color={textColor}>
        {t("Classes")} ({classes.length})
      </Text>

      <VStack align="stretch" spacing={2}>
        {classes.map((cls) => (
          <HStack key={cls.id} justify="space-between" spacing={2}>
            {/* `minW={0}` pozwala nazwie się zawijać zamiast rozpychać wiersz. Realne
                pliki klas mają nazwy w rodzaju `pojazd_transportowy_kategoria_iii_
                ciagnik_siodlowy_z_naczepa` — 60 znaków bez spacji, więc bez tego
                wypychałyby przycisk usuwania poza panel. */}
            <HStack minW={0} flex="1" spacing={2}>
              <ColorPicker
                color={cls.color}
                label={`${t("Class color")}: ${cls.name}`}
                onChange={(color) => onChangeColor(cls.id, color)}
              />
              <Text fontSize="sm" color={textColor} wordBreak="break-word">
                {cls.hotkey ? `[${cls.hotkey}] ` : ""}
                {cls.name}
              </Text>
            </HStack>
            <IconButton
              aria-label={t("Delete class")}
              icon={<MdDelete />}
              size="xs"
              variant="ghost"
              flexShrink={0}
              onClick={() => onDelete(cls.id)}
            />
          </HStack>
        ))}
        <HStack>
          <ColorPicker
            color={newColor}
            label={t("Color of the new class")}
            onChange={setNewColor}
          />
          <Input
            size="sm"
            placeholder={t("New class name")}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          />
          <IconButton
            aria-label={t("Add class")}
            icon={<MdAdd />}
            size="sm"
            onClick={handleAdd}
          />
        </HStack>
      </VStack>
    </VStack>
  );
}
